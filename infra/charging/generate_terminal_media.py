"""Generate original, silent HLS test patterns; never download third-party video."""
import argparse
from e2e_native import Lab
from lab_command import get_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    if not parser.parse_args().execute:
        parser.error('--execute required')
    settings = get_settings()
    core = Lab(settings, settings.ssh_port)
    try:
        core.run(['ffmpeg', '-version'])
        root = '/opt/maestro-terminal-media'
        # Immutable generation: do not replace a running asset set.
        core.run(['test', '!', '-e', root])
        stage = core.run(['mktemp', '-d', '/home/emsadmin/terminal-media-XXXXXX']).strip()
        for profile, size, rate in [('720p', '1280x720', '2000k'), ('1080p', '1920x1080', '4000k')]:
            directory = stage + '/' + profile
            core.run(['mkdir', directory])
            core.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
                      '-f', 'lavfi', '-i', 'testsrc2=size=' + size + ':rate=25',
                      '-t', '60', '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-threads', '2',
                      '-pix_fmt', 'yuv420p', '-b:v', rate, '-minrate', rate, '-maxrate', rate,
                      '-bufsize', rate, '-x264-params', 'nal-hrd=cbr:force-cfr=1',
                      '-g', '50', '-keyint_min', '50', '-sc_threshold', '0',
                      '-f', 'hls', '-hls_time', '2', '-hls_playlist_type', 'vod',
                      '-hls_segment_type', 'fmp4', '-hls_fmp4_init_filename', 'init.mp4',
                      '-hls_segment_filename', directory + '/seg%03d.m4s', directory + '/index.m3u8'], timeout=180)
            print('Generated ' + profile, flush=True)
        core.run(['cp', '-a', stage, root], sudo=True)
        core.run(['chown', '-R', 'root:root', root], sudo=True)
        core.run(['chmod', '-R', 'a+rX', root], sudo=True)
        print('Installed original 60-second patterns, 2-second fMP4 segments')
    finally:
        core.client.close()


if __name__ == '__main__':
    main()
