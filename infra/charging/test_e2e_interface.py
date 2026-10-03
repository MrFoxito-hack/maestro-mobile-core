from e2e_native import confirmed_interface


def test_only_process_confirmed_tun_is_selected():
    interfaces = [
        {'ifname': 'uesimtun3', 'addr_info': [{'local': '10.45.0.3'}]},
        {'ifname': 'uesimtun2', 'addr_info': [{'local': '10.45.0.2'}]},
    ]
    assert confirmed_interface('', interfaces) is None
    log = 'Connection setup for PDU session[1] is successful, TUN interface[uesimtun2, 10.45.0.2] is up.'
    assert confirmed_interface(log, interfaces) == 'uesimtun2'
    assert confirmed_interface(log, interfaces[:1]) is None
    interfaces[1]['addr_info'][0]['local'] = '10.45.0.99'
    assert confirmed_interface(log, interfaces) is None
