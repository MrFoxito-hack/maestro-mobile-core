/* SPDX-License-Identifier: AGPL-3.0-or-later
 * Native asynchronous NWDAF policy controller. N7 ACK is not enforcement proof.
 * Explicit opt-in credential file; runs on the PCF event thread, no worker race.
 */
#include "context.h"
#include "nwdaf-handler.h"
#include "sbi-path.h"
#include <math.h>
#include <ctype.h>
#include <errno.h>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <fcntl.h>

#define NWDAF_RULE "maestro-nwdaf-internet"
#define NWDAF_QUEUE_SIZE 512
typedef struct {
    ogs_pool_id_t sess_id;
    char decision_id[37];
    char supi[32];
    bool throttle;
    ogs_time_t started;
    bool mml;
    struct sockaddr_un peer;
    socklen_t peer_len;
} nwdaf_transaction_t;

typedef struct pcf_nwdaf_context_s {
    ogs_sbi_client_t *client;
    ogs_timer_t *timer;
    char authorization[1100];
    char *query_uri;
    unsigned int sst;
    char sd[7];
    bool pending;
    bool stopping;
    bool congested;
    bool enabled;
    bool audit_pending;
    char *audit_queue[NWDAF_QUEUE_SIZE];
    unsigned audit_head, audit_count;
    FILE *audit_file;
    nwdaf_transaction_t *sending;
    ogs_time_t last_valid;
} pcf_nwdaf_context_t;

static pcf_nwdaf_context_t nwdaf;
static void poll_analytics(void *data);
static void send_audit(void);
static int mml_fd = -1;
static void mml_reply(struct sockaddr_un *peer, socklen_t len, bool ok, const char *detail);

static void new_uuid(char output[37])
{
    ogs_uuid_t uuid;
    ogs_uuid_get(&uuid);
    ogs_uuid_format(output, &uuid);
}

static bool audit(nwdaf_transaction_t *tx, const char *stage)
{
    cJSON *body = cJSON_CreateObject(), *slice = cJSON_CreateObject();
    char event_id[37], reference[160];
    char *encoded;
    new_uuid(event_id);
    if (nwdaf.audit_count >= NWDAF_QUEUE_SIZE) {
        cJSON_Delete(body); cJSON_Delete(slice);
        ogs_error("NWDAF audit queue full; new actuation blocked");
        return false;
    }
    cJSON_AddStringToObject(body, "event_id", event_id);
    cJSON_AddStringToObject(body, "decision_id", tx->decision_id);
    cJSON_AddStringToObject(body, "stage", stage);
    cJSON_AddStringToObject(body, "supi", tx->supi);
    cJSON_AddNumberToObject(slice, "sst", nwdaf.sst);
    cJSON_AddStringToObject(slice, "sd", nwdaf.sd);
    cJSON_AddItemToObject(body, "snssai", slice);
    cJSON_AddStringToObject(body, "policy_id", NWDAF_RULE);
    cJSON_AddStringToObject(body, "action", tx->throttle ? "mitigate" : "restore");
    cJSON_AddNumberToObject(body, "nominal_mbr_bps", 20000000);
    cJSON_AddNumberToObject(body, "target_mbr_bps", tx->throttle ? 5000000 : 20000000);
    snprintf(reference, sizeof(reference), "pcf-native:%s:%s:utc_us=%lld",
            tx->decision_id, stage, (long long)ogs_time_now());
    cJSON_AddStringToObject(body, "evidence_ref", reference);
    if (!strcmp(stage, "N7_ACK") || !strcmp(stage, "FAILED"))
        cJSON_AddNumberToObject(body, "elapsed_ms",
                (ogs_get_monotonic_time()-tx->started)/1000.0);
    encoded = cJSON_PrintUnformatted(body);
    cJSON_Delete(body);
    if (!encoded) return false;
    /* Local append-only spool survives HTTP failures and PCF restarts.
     * The deployment replayer submits records in order; event IDs deduplicate. */
    if (!nwdaf.audit_file || fprintf(nwdaf.audit_file, "%s\n", encoded) < 0 ||
            fflush(nwdaf.audit_file) || fsync(fileno(nwdaf.audit_file))) {
        cJSON_free(encoded);
        ogs_error("NWDAF durable audit write failed");
        return false;
    }
    nwdaf.audit_queue[(nwdaf.audit_head+nwdaf.audit_count)%NWDAF_QUEUE_SIZE] = encoded;
    nwdaf.audit_count++;
    ogs_info("NWDAF decision=%s stage=%s state=%s utc_us=%lld",
            tx->decision_id, stage, tx->throttle ? "congested" : "nominal",
            (long long)ogs_time_now());
    send_audit();
    return true;
}

static int audit_response(int status, ogs_sbi_response_t *response, void *data)
{
    (void)data;
    nwdaf.audit_pending = false;
    if (status == OGS_OK && response && response->status == 201 && nwdaf.audit_count) {
        cJSON_free(nwdaf.audit_queue[nwdaf.audit_head]);
        nwdaf.audit_queue[nwdaf.audit_head] = NULL;
        nwdaf.audit_head = (nwdaf.audit_head+1)%NWDAF_QUEUE_SIZE;
        nwdaf.audit_count--;
        send_audit();
    }
    if (response) ogs_sbi_response_free(response);
    return OGS_OK;
}

static void send_audit(void)
{
    ogs_sbi_request_t *request;
    if (nwdaf.stopping || nwdaf.audit_pending || !nwdaf.audit_count) return;
    request = ogs_sbi_request_new();
    if (!request) return;
    request->h.method = ogs_strdup("POST");
    request->h.uri = ogs_strdup("http://127.0.0.1:8085/management/v1/closed-loop/events");
    request->deadline = ogs_time_from_sec(2);
    request->http.content = ogs_strdup(nwdaf.audit_queue[nwdaf.audit_head]);
    request->http.content_length = strlen(request->http.content);
    ogs_sbi_header_set(request->http.headers, "Authorization", nwdaf.authorization);
    ogs_sbi_header_set(request->http.headers, "Content-Type", "application/json");
    nwdaf.audit_pending = true;
    if (!ogs_sbi_client_send_request(nwdaf.client, audit_response, request, NULL))
        nwdaf.audit_pending = false;
    ogs_sbi_request_free(request);
}

/* Called only by the existing N7 dispatcher while this module is sending.
 * Other AF/PCF notifications keep the original callback and behavior. */
void *pcf_nwdaf_notify_context(pcf_sess_t *sess)
{
    return nwdaf.sending && nwdaf.sending->sess_id == sess->id ? nwdaf.sending : NULL;
}

int pcf_nwdaf_notify_response(int status, ogs_sbi_response_t *response, void *data)
{
    nwdaf_transaction_t *tx = data;
    pcf_sess_t *sess = pcf_sess_find_by_id(tx->sess_id);
    bool accepted = status == OGS_OK && response && response->status == 204;
    if (tx->mml)
        mml_reply(&tx->peer, tx->peer_len, accepted,
                accepted ? "N7_ACK; verifique enforcement en UPF" : "SMF no confirmó N7 (204)");
    else if (!nwdaf.stopping) audit(tx, accepted ? "N7_ACK" : "FAILED");
    if (sess) {
        sess->nwdaf_pending = false;
        sess->nwdaf_changed_at = ogs_get_monotonic_time();
        if (accepted && !tx->mml) sess->nwdaf_throttled = tx->throttle;
    }
    if (response) ogs_sbi_response_free(response);
    ogs_free(tx);
    return OGS_OK;
}

static void update_session(pcf_sess_t *sess, pcf_ue_sm_t *ue, bool throttle)
{
    nwdaf_transaction_t *tx;
    cJSON *json;
    OpenAPI_sm_policy_decision_t *decision;
    char document[2048];
    bool sent;
    if (sess->nwdaf_pending || sess->nwdaf_throttled == throttle ||
            !sess->nsmf.client || !sess->notification_uri ||
            nwdaf.audit_count > NWDAF_QUEUE_SIZE-4) return;
    /* A minimum residence time prevents a single transient sample oscillating
     * the policy; only new validated analytics can request normal restoration. */
    if (sess->nwdaf_changed_at &&
            ogs_get_monotonic_time()-sess->nwdaf_changed_at < ogs_time_from_sec(10)) return;
    tx = ogs_calloc(1, sizeof(*tx));
    if (!tx) return;
    tx->sess_id = sess->id;
    tx->throttle = throttle;
    tx->started = ogs_get_monotonic_time();
    new_uuid(tx->decision_id);
    snprintf(tx->supi, sizeof(tx->supi), "%s", ue->supi);
    snprintf(document, sizeof(document),
        "{\"pccRules\":{\"%s\":{\"pccRuleId\":\"%s\",\"precedence\":100,"
        "\"flowInfos\":[{\"flowDescription\":\"permit out ip from any to assigned\","
        "\"flowDirection\":\"DOWNLINK\"}],\"refQosData\":[\"%s\"]}},"
        "\"qosDecs\":{\"%s\":{\"qosId\":\"%s\",\"5qi\":9,\"priorityLevel\":8,"
        "\"arp\":{\"priorityLevel\":8,\"preemptCap\":\"NOT_PREEMPT\","
        "\"preemptVuln\":\"NOT_PREEMPTABLE\"},"
        "\"maxbrDl\":\"%d Mbps\",\"maxbrUl\":\"20 Mbps\","
        "\"gbrDl\":\"1 Kbps\",\"gbrUl\":\"1 Kbps\"}}}",
        NWDAF_RULE, NWDAF_RULE, NWDAF_RULE, NWDAF_RULE, NWDAF_RULE, throttle ? 5 : 20);
    json = cJSON_Parse(document);
    decision = json ? OpenAPI_sm_policy_decision_parseFromJSON(json) : NULL;
    if (json) cJSON_Delete(json);
    if (!decision) { ogs_free(tx); return; }
    if (!audit(tx, "DETECTED")) {
        OpenAPI_sm_policy_decision_free(decision); ogs_free(tx); return;
    }
    sess->nwdaf_pending = true;
    nwdaf.sending = tx;
    sent = pcf_sbi_send_smpolicycontrol_update_notify(sess, decision);
    nwdaf.sending = NULL;
    OpenAPI_sm_policy_decision_free(decision);
    if (sent) audit(tx, "N7_SENT");
    else {
        audit(tx, "CANCELLED");
        sess->nwdaf_pending = false;
        sess->nwdaf_changed_at = ogs_get_monotonic_time();
        ogs_free(tx);
    }
}

static void apply_load(int load)
{
    pcf_ue_sm_t *ue;
    pcf_sess_t *sess;
    if (!nwdaf.enabled) return;
    if (load > 85) nwdaf.congested = true;
    else if (load < 70) nwdaf.congested = false;
    else return;
    ogs_list_for_each(&pcf_self()->pcf_ue_sm_list, ue) {
        ogs_list_for_each(&ue->sess_list, sess) {
            if (sess->s_nssai.sst != nwdaf.sst ||
                    sess->s_nssai.sd.v != strtoul(nwdaf.sd, NULL, 16) ||
                    !sess->dnn || strcmp(sess->dnn, "internet")) continue;
            update_session(sess, ue, nwdaf.congested);
        }
    }
}

static int analytics_response(int status, ogs_sbi_response_t *response, void *data)
{
    cJSON *root = NULL, *reports, *report, *load, *slices, *slice, *sst;
    cJSON *expiry, *generated, *sd;
    ogs_time_t until, created, now;
    bool valid = false;
    (void)data;
    nwdaf.pending = false;
    if (nwdaf.stopping || status != OGS_OK || !response)
        goto done;
    if (response->status != 200 || !response->http.content ||
            response->http.content_length > 65536)
        goto done;
    root = cJSON_ParseWithLength(response->http.content,
                                response->http.content_length);
    if (!cJSON_IsObject(root)) goto done;
    reports = cJSON_GetObjectItemCaseSensitive(root, "sliceLoadLevelInfos");
    expiry = cJSON_GetObjectItemCaseSensitive(root, "expiry");
    generated = cJSON_GetObjectItemCaseSensitive(root, "timeStampGen");
    if (!cJSON_IsString(expiry) || !cJSON_IsString(generated) ||
            !ogs_sbi_time_from_string(&until, expiry->valuestring) ||
            !ogs_sbi_time_from_string(&created, generated->valuestring)) goto done;
    now = ogs_time_now();
    if (until <= now || created > now || now-created > ogs_time_from_sec(300))
        goto done;
    if (!cJSON_IsArray(reports) || cJSON_GetArraySize(reports) != 1) goto done;
    report = cJSON_GetArrayItem(reports, 0);
    load = cJSON_GetObjectItemCaseSensitive(report, "loadLevelInformation");
    slices = cJSON_GetObjectItemCaseSensitive(report, "snssais");
    if (!cJSON_IsNumber(load) || !isfinite(load->valuedouble) ||
            load->valuedouble < 0 || load->valuedouble > 100 ||
            fabs(load->valuedouble - load->valueint) > 0 ||
            !cJSON_IsArray(slices) || cJSON_GetArraySize(slices) != 1) goto done;
    slice = cJSON_GetArrayItem(slices, 0);
    sst = cJSON_GetObjectItemCaseSensitive(slice, "sst");
    /* SST without SD is not a wildcard matching all differentiators. */
    if (!cJSON_IsNumber(sst) || sst->valuedouble < nwdaf.sst ||
            sst->valuedouble > nwdaf.sst) goto done;
    sd = cJSON_GetObjectItemCaseSensitive(slice, "sd");
    if (nwdaf.sd[0]) {
        if (!cJSON_IsString(sd) || strlen(sd->valuestring) != 6 ||
                strcasecmp(sd->valuestring, nwdaf.sd)) goto done;
    } else if (sd) goto done;
    valid = true;
    nwdaf.last_valid = ogs_get_monotonic_time();
    apply_load(load->valueint);
    ogs_info("NWDAF load=%d state=%s actuation=%s",
            load->valueint, nwdaf.congested ? "congested" : "nominal",
            nwdaf.enabled ? "enabled" : "disabled");
done:
    if (!valid && !nwdaf.stopping)
        ogs_debug("NWDAF observation unavailable or invalid; no policy change");
    if (root) cJSON_Delete(root);
    if (response) ogs_sbi_response_free(response);
    return OGS_OK;
}

#include "mml-control.inc"

static void poll_analytics(void *data)
{
    ogs_sbi_request_t *request;
    (void)data;
    if (nwdaf.stopping) return;
    ogs_timer_start(nwdaf.timer, ogs_time_from_sec(1));
    mml_poll();
    send_audit();
    /* Restore our own throttled rule if analytics disappear for 30 s. */
    if (nwdaf.last_valid && ogs_get_monotonic_time()-nwdaf.last_valid > ogs_time_from_sec(30))
        apply_load(0);
    if (nwdaf.pending) return;
    request = ogs_sbi_request_new();
    if (!request) return;
    request->h.method = ogs_strdup("GET");
    request->h.uri = ogs_strdup(nwdaf.query_uri);
    request->deadline = ogs_time_from_sec(3);
    ogs_sbi_header_set(request->http.headers, "Authorization", nwdaf.authorization);
    nwdaf.pending = true;
    if (!ogs_sbi_client_send_request(nwdaf.client, analytics_response, request, NULL))
        nwdaf.pending = false;
    ogs_sbi_request_free(request);
}

int pcf_nwdaf_open(void)
{
    const char *path = getenv("MAESTRO_NWDAF_TOKEN_FILE");
    const char *sst_text = getenv("MAESTRO_NWDAF_SST");
    const char *sd_text = getenv("MAESTRO_NWDAF_SD");
    const char *actuate = getenv("MAESTRO_NWDAF_ACTUATE");
    const char *audit_path = getenv("MAESTRO_NWDAF_AUDIT_FILE");
    char *end = NULL;
    unsigned long parsed_sst;
    size_t i;
    char token[1026];
    char endpoint[] = "http://127.0.0.1:8085";
    FILE *file;
    size_t len;
    OpenAPI_uri_scheme_e scheme;
    char *fqdn = NULL;
    uint16_t port = 0;
    ogs_sockaddr_t *addr = NULL, *addr6 = NULL;
    memset(&nwdaf, 0, sizeof(nwdaf));
    if (!path) return OGS_OK;
    nwdaf.sst = 1;
    if (sst_text) {
        if (!*sst_text || strspn(sst_text, "0123456789") != strlen(sst_text))
            return OGS_ERROR;
        errno = 0;
        parsed_sst = strtoul(sst_text, &end, 10);
        if (errno || *end || parsed_sst > 255) return OGS_ERROR;
        nwdaf.sst = (unsigned int)parsed_sst;
    }
    if (sd_text) {
        if (strlen(sd_text) != 6 || strspn(sd_text, "0123456789abcdefABCDEF") != 6)
            return OGS_ERROR;
        for (i = 0; i < 6; i++) nwdaf.sd[i] = (char)toupper((unsigned char)sd_text[i]);
    }
    nwdaf.enabled = actuate && !strcmp(actuate, "1");
    if (nwdaf.enabled) {
        /* Only the explicitly approved Internet slice is actuated. */
        if (nwdaf.sst != 1 || strcmp(nwdaf.sd, "000001") || !audit_path)
            return OGS_ERROR;
        nwdaf.audit_file = fopen(audit_path, "a");
        if (!nwdaf.audit_file) return OGS_ERROR;
    }
    file = fopen(path, "r");
    if (!file) return OGS_ERROR;
    len = fread(token, 1, sizeof(token)-1, file);
    fclose(file);
    if (len < 24 || len > 1024) return OGS_ERROR;
    token[len] = 0;
    if (strspn(token, "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_") != len)
        return OGS_ERROR;
    snprintf(nwdaf.authorization, sizeof(nwdaf.authorization), "Bearer %s", token);
    memset(token, 0, sizeof(token));
    if (!ogs_sbi_getaddr_from_uri(&scheme, &fqdn, &port, &addr, &addr6,
            endpoint)) return OGS_ERROR;
    nwdaf.client = ogs_sbi_client_add(scheme, fqdn, port, addr, addr6);
    if (fqdn) ogs_free(fqdn);
    if (addr) ogs_freeaddrinfo(addr);
    if (addr6) ogs_freeaddrinfo(addr6);
    if (!nwdaf.client) return OGS_ERROR;
    nwdaf.query_uri = ogs_msprintf(
        "http://127.0.0.1:8085/nnwdaf-analyticsinfo/v1/analytics"
        "?event-id=LOAD_LEVEL_INFORMATION&event-filter="
        "%%7B%%22snssais%%22%%3A%%5B%%7B%%22sst%%22%%3A%u%s%s%s%%7D%%5D%%7D",
        nwdaf.sst, nwdaf.sd[0] ? "%2C%22sd%22%3A%22" : "",
        nwdaf.sd, nwdaf.sd[0] ? "%22" : "");
    if (!nwdaf.query_uri) { pcf_nwdaf_close(); return OGS_ERROR; }
    nwdaf.timer = ogs_timer_add(ogs_app()->timer_mgr, poll_analytics, NULL);
    if (!nwdaf.timer) { pcf_nwdaf_close(); return OGS_ERROR; }
    ogs_timer_start(nwdaf.timer, ogs_time_from_sec(1));
    mml_open();
    ogs_info("NWDAF SBI http://127.0.0.1:8085 ready; native N7 actuation=%s slice=%u/%s",
            nwdaf.enabled ? "enabled" : "disabled", nwdaf.sst, nwdaf.sd);
    return OGS_OK;
}

void pcf_nwdaf_close(void)
{
    unsigned i;
    nwdaf.stopping = true;
    if (mml_fd >= 0) {
        close(mml_fd);
        mml_fd = -1;
        unlink("/var/lib/open5gs/nwdaf/mml.sock");
    }
    if (nwdaf.timer) { ogs_timer_delete(nwdaf.timer); nwdaf.timer = NULL; }
    if (nwdaf.client) {
        ogs_sbi_client_stop(nwdaf.client);
        ogs_sbi_client_remove(nwdaf.client);
        nwdaf.client = NULL;
    }
    memset(nwdaf.authorization, 0, sizeof(nwdaf.authorization));
    if (nwdaf.query_uri) { ogs_free(nwdaf.query_uri); nwdaf.query_uri = NULL; }
    for (i = 0; i < NWDAF_QUEUE_SIZE; i++) {
        if (nwdaf.audit_queue[i]) cJSON_free(nwdaf.audit_queue[i]);
        nwdaf.audit_queue[i] = NULL;
    }
    if (nwdaf.audit_file) { fclose(nwdaf.audit_file); nwdaf.audit_file = NULL; }
}
