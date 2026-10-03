/* SPDX-License-Identifier: AGPL-3.0-or-later
 * Privileged local control, drained on the PCF event thread. No C context is
 * touched from a worker. A QoS response is emitted only after the SMF N7 reply.
 */
static void mml_reply(struct sockaddr_un *peer, socklen_t len, bool ok, const char *detail)
{
    cJSON *body = cJSON_CreateObject();
    char *encoded;
    cJSON_AddStringToObject(body, "status", ok ? "success" : "failed");
    cJSON_AddStringToObject(body, "detail", detail);
    cJSON_AddStringToObject(body, "mode", nwdaf.enabled ? "AUTONOMOUS" : "MANUAL");
    encoded = cJSON_PrintUnformatted(body);
    if (encoded && mml_fd >= 0)
        sendto(mml_fd, encoded, strlen(encoded), MSG_DONTWAIT, (struct sockaddr *)peer, len);
    cJSON_free(encoded);
    cJSON_Delete(body);
}

static void mml_open(void)
{
    struct sockaddr_un addr = {0};
    mode_t previous;
    const char *enabled = getenv("MAESTRO_MML_CONTROL");
    if (!enabled || strcmp(enabled, "1")) return;
    addr.sun_family = AF_UNIX;
    snprintf(addr.sun_path, sizeof(addr.sun_path), "/var/lib/open5gs/nwdaf/mml.sock");
    mml_fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (mml_fd < 0) return;
    unlink(addr.sun_path);
    previous = umask(0077);
    if (bind(mml_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        close(mml_fd);
        mml_fd = -1;
        ogs_error("MML control socket unavailable");
    }
    umask(previous);
}

static bool mml_rate(cJSON *value)
{
    return !value || (cJSON_IsNumber(value) && isfinite(value->valuedouble) &&
            value->valuedouble >= 0.001 && value->valuedouble <= 100000);
}

/* Read-only controller snapshot, serialized on the PCF event thread. These are
 * controller observations, NOT the SMF's effective policy or the UPF's QERs.
 * The initial nwdaf_throttled=false must never be called a measured 20 Mbps MBR.
 * Bound the reply: an incomplete snapshot is explicitly unusable for admission.
 */
static void mml_laboratory_snapshot(struct sockaddr_un *peer, socklen_t peer_len)
{
    cJSON *body = cJSON_CreateObject(), *sessions = cJSON_CreateArray();
    pcf_ue_sm_t *ue;
    pcf_sess_t *sess;
    char *encoded;
    unsigned count = 0, pending = 0;
    bool truncated = false;
    cJSON_AddStringToObject(body, "status", "success");
    cJSON_AddNumberToObject(body, "schema_version", 1);
    cJSON_AddStringToObject(body, "scope", "pcf_nwdaf_controller_only");
    cJSON_AddStringToObject(body, "mode", nwdaf.enabled ? "AUTONOMOUS" : "MANUAL");
    cJSON_AddNumberToObject(body, "pid", getpid());
    cJSON_AddNumberToObject(body, "monotonic_us", (double)ogs_get_monotonic_time());
    cJSON_AddBoolToObject(body, "analytics_request_pending", nwdaf.pending);
    cJSON_AddBoolToObject(body, "congested", nwdaf.congested);
    cJSON_AddBoolToObject(body, "effective_policy_available", false);
    cJSON_AddBoolToObject(body, "all_policy_writers_observed", false);
    cJSON_AddItemToObject(body, "sessions", sessions);
    ogs_list_for_each(&pcf_self()->pcf_ue_sm_list, ue) {
        ogs_list_for_each(&ue->sess_list, sess) {
            cJSON *item;
            char sd[7];
            if (!sess->dnn || strcmp(sess->dnn, "internet")) continue;
            count++;
            if (sess->nwdaf_pending) pending++;
            if (count > 64) { truncated = true; continue; }
            item = cJSON_CreateObject();
            snprintf(sd, sizeof(sd), "%06x", (unsigned)sess->s_nssai.sd.v);
            cJSON_AddStringToObject(item, "supi", ue->supi);
            cJSON_AddNumberToObject(item, "pcf_session_id", sess->id);
            cJSON_AddStringToObject(item, "dnn", sess->dnn);
            cJSON_AddNumberToObject(item, "sst", sess->s_nssai.sst);
            cJSON_AddStringToObject(item, "sd", sd);
            cJSON_AddBoolToObject(item, "nwdaf_or_mml_pending", sess->nwdaf_pending);
            cJSON_AddBoolToObject(item, "controller_throttled_flag", sess->nwdaf_throttled);
            cJSON_AddNumberToObject(item, "controller_changed_monotonic_us", (double)sess->nwdaf_changed_at);
            cJSON_AddItemToArray(sessions, item);
        }
    }
    cJSON_AddNumberToObject(body, "internet_session_count", count);
    cJSON_AddNumberToObject(body, "controller_pending_count", pending);
    cJSON_AddBoolToObject(body, "truncated", truncated);
    encoded = cJSON_PrintUnformatted(body);
    if (encoded && strlen(encoded) <= 60000 && mml_fd >= 0)
        sendto(mml_fd, encoded, strlen(encoded), MSG_DONTWAIT, (struct sockaddr *)peer, peer_len);
    else
        mml_reply(peer, peer_len, false, "Snapshot unavailable or exceeds bounded response");
    cJSON_free(encoded);
    cJSON_Delete(body);
}

static void mml_poll(void)
{
    char buffer[4096], document[2048], dl_field[80] = "", ul_field[80] = "";
    struct sockaddr_un peer;
    socklen_t peer_len = sizeof(peer);
    ssize_t size;
    cJSON *root, *op, *mode, *supi, *qi, *dl, *ul, *json;
    pcf_ue_sm_t *ue, *selected_ue = NULL;
    pcf_sess_t *sess, *selected = NULL;
    OpenAPI_sm_policy_decision_t *decision;
    nwdaf_transaction_t *tx;
    unsigned matches = 0;
    bool sent;
    if (mml_fd < 0) return;
    size = recvfrom(mml_fd, buffer, sizeof(buffer), MSG_DONTWAIT,
                    (struct sockaddr *)&peer, &peer_len);
    if (size <= 0) return;
    if (size >= (ssize_t)sizeof(buffer)) {
        mml_reply(&peer, peer_len, false, "Solicitud demasiado grande"); return;
    }
    buffer[size] = 0;
    root = cJSON_Parse(buffer);
    op = cJSON_GetObjectItemCaseSensitive(root, "operation");
    if (!cJSON_IsString(op)) goto invalid;
    if (!strcmp(op->valuestring, "laboratory_snapshot")) {
        mml_laboratory_snapshot(&peer, peer_len);
        goto done;
    }
    if (!strcmp(op->valuestring, "status")) {
        mml_reply(&peer, peer_len, true, "Estado operativo del actuador PCF");
        goto done;
    }
    if (!strcmp(op->valuestring, "mode")) {
        mode = cJSON_GetObjectItemCaseSensitive(root, "mode");
        if (!cJSON_IsString(mode)) goto invalid;
        if (!strcmp(mode->valuestring, "AUTONOMOUS")) {
            if (!nwdaf.audit_file || nwdaf.sst != 1 || strcmp(nwdaf.sd, "000001")) {
                mml_reply(&peer, peer_len, false, "Actuación autónoma no configurada");
                goto done;
            }
            nwdaf.enabled = true;
        } else if (!strcmp(mode->valuestring, "MANUAL")) nwdaf.enabled = false;
        else goto invalid;
        mml_reply(&peer, peer_len, true, "Modo operativo aplicado al actuador PCF");
        goto done;
    }
    if (strcmp(op->valuestring, "qos")) goto invalid;
    supi = cJSON_GetObjectItemCaseSensitive(root, "supi");
    qi = cJSON_GetObjectItemCaseSensitive(root, "five_qi");
    dl = cJSON_GetObjectItemCaseSensitive(root, "mbr_dl_mbps");
    ul = cJSON_GetObjectItemCaseSensitive(root, "mbr_ul_mbps");
    if (!cJSON_IsString(supi) || strncmp(supi->valuestring, "imsi-", 5) ||
            strlen(supi->valuestring) < 19 || strlen(supi->valuestring) > 20 ||
            strspn(supi->valuestring + 5, "0123456789") != strlen(supi->valuestring + 5) ||
            !cJSON_IsNumber(qi) || !isfinite(qi->valuedouble) || qi->valueint < 1 ||
            qi->valueint > 255 || fabs(qi->valuedouble - qi->valueint) > 0 ||
            !mml_rate(dl) || !mml_rate(ul)) goto invalid;
    ogs_list_for_each(&pcf_self()->pcf_ue_sm_list, ue) {
        if (strcmp(ue->supi, supi->valuestring)) continue;
        ogs_list_for_each(&ue->sess_list, sess) {
            if (!sess->dnn || strcmp(sess->dnn, "internet")) continue;
            selected = sess; selected_ue = ue; matches++;
        }
    }
    if (matches != 1 || selected->nwdaf_pending || !selected->nsmf.client || !selected->notification_uri) {
        mml_reply(&peer, peer_len, false, "Requiere una sesión Internet única, activa y sin N7 pendiente");
        goto done;
    }
    if (dl) snprintf(dl_field, sizeof(dl_field), ",\"maxbrDl\":\"%.6f Mbps\"", dl->valuedouble);
    if (ul) snprintf(ul_field, sizeof(ul_field), ",\"maxbrUl\":\"%.6f Mbps\"", ul->valuedouble);
    snprintf(document, sizeof(document),
        "{\"pccRules\":{\"maestro-mml\":{\"pccRuleId\":\"maestro-mml\",\"precedence\":90,"
        "\"flowInfos\":[{\"flowDescription\":\"permit out ip from any to assigned\",\"flowDirection\":\"DOWNLINK\"},"
        "{\"flowDescription\":\"permit in ip from assigned to any\",\"flowDirection\":\"UPLINK\"}],"
        "\"refQosData\":[\"maestro-mml\"]}},\"qosDecs\":{\"maestro-mml\":{\"qosId\":\"maestro-mml\","
        "\"5qi\":%d,\"arp\":{\"priorityLevel\":8,\"preemptCap\":\"NOT_PREEMPT\","
        "\"preemptVuln\":\"NOT_PREEMPTABLE\"}%s%s}}}", qi->valueint, dl_field, ul_field);
    json = cJSON_Parse(document);
    decision = json ? OpenAPI_sm_policy_decision_parseFromJSON(json) : NULL;
    cJSON_Delete(json);
    if (!decision) goto invalid;
    tx = ogs_calloc(1, sizeof(*tx));
    if (!tx) { OpenAPI_sm_policy_decision_free(decision); goto invalid; }
    tx->sess_id = selected->id;
    tx->mml = true;
    tx->peer = peer;
    tx->peer_len = peer_len;
    tx->started = ogs_get_monotonic_time();
    new_uuid(tx->decision_id);
    snprintf(tx->supi, sizeof(tx->supi), "%s", selected_ue->supi);
    selected->nwdaf_pending = true;
    nwdaf.sending = tx;
    sent = pcf_sbi_send_smpolicycontrol_update_notify(selected, decision);
    nwdaf.sending = NULL;
    OpenAPI_sm_policy_decision_free(decision);
    if (!sent) {
        selected->nwdaf_pending = false;
        ogs_free(tx);
        mml_reply(&peer, peer_len, false, "No se pudo enviar N7");
    }
    goto done;
invalid:
    mml_reply(&peer, peer_len, false, "Parámetros de control inválidos");
done:
    cJSON_Delete(root);
}
