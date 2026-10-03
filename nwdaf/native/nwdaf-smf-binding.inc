/* SPDX-License-Identifier: AGPL-3.0-or-later
 * Operator full-Internet PCC profile: bind the DL aggregate limit to the
 * already active default QoS flow. Do not create a new QFI/FAR requiring RAN
 * resource activation. Preserve UL, 5QI, ARP, GBR, filters, URRs and TEIDs.
 * Included inside smf_qos_flow_binding after validating pcc_rule->id.
 */
        if (pcc_rule->type == OGS_PCC_RULE_TYPE_INSTALL &&
                !strcmp(pcc_rule->id, "maestro-nwdaf-internet")) {
            smf_bearer_t *existing;
            uint64_t rate = pcc_rule->qos.mbr.downlink;
            if (sess->s_nssai.sst != 1 || sess->s_nssai.sd.v != 1 ||
                    !sess->session.name || strcmp(sess->session.name, "internet") ||
                    (rate != 5000000 && rate != 20000000) ||
                    pcc_rule->num_of_flow != 1 ||
                    pcc_rule->flow[0].direction != OGS_FLOW_DOWNLINK_ONLY ||
                    !pcc_rule->flow[0].description ||
                    strcmp(pcc_rule->flow[0].description,
                           "permit out ip from any to assigned")) {
                ogs_error("NWDAF PCC profile rejected: wrong slice, DNN, rate or filter");
                continue;
            }
            existing = smf_default_bearer_in_sess(sess);
            if (!existing || !existing->qer || !existing->dl_pdr ||
                    !existing->dl_pdr->far) {
                ogs_error("NWDAF PCC profile requires an established default QoS flow");
                continue;
            }
            if (existing->qer->mbr.downlink == rate) continue;
            existing->qos.mbr.downlink = rate;
            existing->qer->mbr.downlink = rate;
            ogs_list_add(&sess->qos_flow_to_modify_list, &existing->to_modify_node);
            pfcp_flags |= OGS_PFCP_MODIFY_QOS_MODIFY;
            ogs_info("NWDAF PCC bound to existing QFI=%u QER=%u DL=%llu bps",
                    existing->qfi, existing->qer->id, (unsigned long long)rate);
            continue;
        }
