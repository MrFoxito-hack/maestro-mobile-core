"""Read one NF on its event thread; no policy or charging mutations."""
import argparse
import json
import socket
import uuid


def observe(path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
        client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 524288)
        client.settimeout(3)
        client.bind('\0maestro-native-observe-' + uuid.uuid4().hex)
        client.sendto(b'observe\n', path)
        raw, _, flags, _ = client.recvmsg(196609)
        if flags & socket.MSG_TRUNC or len(raw) > 196608:
            raise ValueError('native_observation_truncated')
        result = json.loads(raw)
        if result.get('status') != 'success':
            raise ValueError(result.get('error_code', 'native_observation_failed'))
        data = result['data']
        if data.get('scope') != 'native_observation' or data.get('schema_version') != 1:
            raise ValueError('native_observation_protocol_mismatch')
        return data


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('socket')
    args = parser.parse_args()
    print(json.dumps(observe(args.socket)))
