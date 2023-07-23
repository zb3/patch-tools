import argparse
import binascii
import os
import mmap
import sys

from collections import defaultdict

from utils import LineReader, to_file

CHUNK_SIZE = 16
MIN_NON_DIFF_BYTES = 16

def _find_difference_end(bytes1, bytes2, i, total_len):
    while True:
        while i < total_len and bytes1[i] != bytes2[i]:
            i += 1

        if i == total_len:
            return i

        common_start = i
        while i < total_len and i < common_start + MIN_NON_DIFF_BYTES and bytes1[i] == bytes2[i]:
            i += 1

        if i == total_len or i == common_start + MIN_NON_DIFF_BYTES:
            return common_start


def diff(file1, file2, basedir='.'):
    with open(os.path.join(basedir, file1), "rb") as f1, open(os.path.join(basedir, file2), "rb") as f2:
        with mmap.mmap(f1.fileno(), 0, access=mmap.ACCESS_READ) as bytes1, mmap.mmap(f2.fileno(), 0, access=mmap.ACCESS_READ) as bytes2:

          total_len = min(len(bytes1), len(bytes2))

          i = 0
          while i < total_len:
              if bytes1[i] == bytes2[i]:
                  i += 1
                  continue

              start = i
              i = _find_difference_end(bytes1, bytes2, i, total_len)

              yield start, bytes1[start:i], bytes2[start:i]

          if i < max(len(bytes1), len(bytes2)):
              if len(bytes1) == total_len:
                  yield i, None, bytes2[i:]
              else:
                  yield i, bytes1[i:], None


def _chunks(n, size):
    pos = 0
    while pos < n:
        yield pos, pos + size
        pos += size


def diff_command(diff_dir, file_groups, write_line):
    for file1, file2, target_name in file_groups:
        shown = False
        for start, before, after in diff(file1, file2, basedir=diff_dir):
            if not shown:
                write_line(f">> {target_name}")
                shown = True

            write_line(f"@0x{start:08x}")

            for data in [before, after]:
                if data:
                    for lstart, lend in _chunks(len(data), CHUNK_SIZE):
                        prefix = "- " if data is before else "+ "
                        write_line(prefix + " ".join(f"{byte:02x}" for byte in data[lstart:lend]))

def parse_diff(reader, unpatch=False):
    patches = defaultdict(lambda: defaultdict(lambda: {'old': b'', 'new': b''}))  
    current_file = None
    current_offset = None

    in_multiline_comment = False

    for line in reader.get_lines():
        line = line.strip()
        
        if in_multiline_comment:
            if '*/' in line:
                in_multiline_comment = False
            continue

        if '/*' in line:
            in_multiline_comment = True
            continue
                
        if not line or line.startswith('#'):
            continue

        if line.startswith('>> '):
            if current_offset is not None:
                # append pending offset
                patches[current_file][current_offset] = {"old": old_bytes, "new": new_bytes}
            current_file = line[3:]
            current_offset = None
        elif line.startswith('@0x'):
            if current_offset is not None:
                # append pending offset
                patches[current_file][current_offset] = {"old": old_bytes, "new": new_bytes}
            current_offset = int(line[3:], 16)
            old_bytes = new_bytes = b''
        elif line.startswith('+ ' if unpatch else '- '):
            old_bytes += bytes.fromhex(line[2:])
        elif line.startswith('- ' if unpatch else '+ '):
            new_bytes += bytes.fromhex(line[2:])

    # append last offset
    if current_file and current_offset is not None:
        patches[current_file][current_offset] = {"old": old_bytes, "new": new_bytes}

    return patches

def verify_patches(patches, diff_dir='.'):
    for filename, patches_for_file in patches.items():
        with open(os.path.join(diff_dir, filename), 'r+b') as file:
            for offset, patch in sorted(patches_for_file.items()):
                file.seek(offset)

                old_bytes = patch['old']
                if file.read(len(old_bytes)) != old_bytes:
                    raise ValueError(f'{filename}: bytes not equal at {offset:#0{4}x}.')


def apply_patches(patches, diff_dir='.'):
    for filename, patches_for_file in patches.items():
        with open(os.path.join(diff_dir, filename), 'r+b') as file:
            for offset, patch in sorted(patches_for_file.items()):
                old_bytes, new_bytes = patch["old"], patch["new"]
                if not new_bytes:
                    continue

                print(f'{filename}: writing {len(new_bytes)} bytes at {offset:#0{4}x}', file=sys.stderr)
                file.seek(offset)
                file.write(new_bytes)

            if len(new_bytes) < len(old_bytes):  # bytes were removed and not fully replaced
                num_to_truncate = len(old_bytes) - len(new_bytes)
                print(f'{filename}: removing {num_to_truncate} bytes at the end', file=sys.stderr)
                file.truncate(offset + len(new_bytes))  # truncate file


def patch_command(reader, diff_dir='.', unpatch=False):
    patches = parse_diff(reader, unpatch)

    verify_patches(patches, diff_dir)
    apply_patches(patches, diff_dir)

def custom_parser(args):
    if not args or args[0] in ('-h', '--help') or len(args) % 3:
        print(diff_help_text, file=sys.stderr)
        exit(1)

    parsed_args = []

    for x in range(0, len(args), 3):
        original, patched, path = args[x:x+3]

        if path in ('-o', '--orig', '--original'):
          path = original

        elif path in ('-p', '--patch', '--patched'):
          path = patched

        parsed_args.append((original, patched, path))

    return parsed_args


diff_help_text = f'''
usage: {sys.argv[0]} diff [-h] [-d PATCH_DIR] [file groups]...

options:
  -h, --help            show this help message and exit
  -d PATCH_DIR, --patch-dir PATCH_DIR
                        Directory to apply patches

file group syntax:
  each file group consists of 3 arguments - the original file, the patched file and the target path (the file name in the patch file).

  for convenience, the target path might also be:
  -o, --original        to use the path of the original file as the target file path
  -p, --patched         to use the path of the pathed file as the target file path

  examples:
  python {sys.argv[0]} diff /tmp/file.original /opt/file -p
  python {sys.argv[0]} diff /opt/file /tmp/file.patched -o
  python {sys.argv[0]} diff /tmp/file.original /tmp/file.patched /opt/file

  more than one group can be specified in order to generate diff for multiple files, for example:
  python {sys.argv[0]} diff /tmp/file1.original /opt/file1 -p \\
     /opt/file2 /tmp/file2.patched -o
'''


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command', required=True)

    diff_parser = subparsers.add_parser('diff', add_help=False)
    diff_parser.add_argument('-d', '--patch-dir', default='.', help='Patch base directory')

    patch_parser = subparsers.add_parser('patch')
    patch_parser.add_argument('-d', '--patch-dir', default='.', help='Directory to apply patches')
    patch_parser.add_argument('-u', '--unpatch', action='store_true', help='Experimental unpatch mode')

    args, remainder = parser.parse_known_args()
    # print(args, remainder)

    if args.command == 'diff':
        file_groups = custom_parser(remainder)
        diff_command(args.patch_dir, file_groups, to_file())

    elif args.command == 'patch':
        try:
            patch_command(LineReader(sys.stdin), args.patch_dir, args.unpatch)
        except ValueError as ve:
            print(f'Error: {ve.args[0]}', file=sys.stderr)
            print('Aborting.')
            exit(1)

if __name__ == '__main__':
    main()

