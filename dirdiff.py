import argparse
from itertools import chain
from pprint import pprint
import re
import stat
import subprocess
import os
import sys
import gzip
import base64
import shutil

from bindiff import diff_command as bindiff_diff, parse_diff as bindiff_parse_diff, verify_patches as bindiff_verify, apply_patches as bindiff_apply
from utils import LineReader, escape_text_line, to_file, unescape_text_line, with_indent


"""
# currently not meant to be reversible
# remove files should not include those in removed directories

lateron:
  - fifos?
"""

BUFSIZE = 2*1024*1024

def compare_file_bytes(f1, f2):
    bufsize = BUFSIZE
    is_binary = False

    with open(f1, 'rb') as fp1, open(f2, 'rb') as fp2:
        while True:
            b1 = fp1.read(bufsize)
            b2 = fp2.read(bufsize)

            if not is_binary and (b'\x00' in b1 or b'\x00' in b2):
                is_binary = True

            if b1 != b2:
                return False, is_binary
            if not b1:
                return True, is_binary


def is_file_binary(f1):
    bufsize = BUFSIZE

    with open(f1, 'rb') as fp1:
        while True:
            b1 = fp1.read(bufsize)

            if b'\x00' in b1:
                return True

            if not b1:
                return False



def retrieve_changed_files(dir1, dir2, candidates):
    ret = {
        'text_files': [],
        'binary_files': []
    }

    for fname in candidates:
        diskpath1 = os.path.join(dir1, fname)
        diskpath2 = os.path.join(dir2, fname)

        # we don't compare their sizes, because even if we know that they differ
        # we need to check if they're binary

        same, is_binary = compare_file_bytes(diskpath1, diskpath2)

        if not same:
            # further match logic here(?)
            if is_binary:
                ret['binary_files'].append(fname)
            else:
                ret['text_files'].append(fname)

    return ret


def get_file_type(path):
    st = os.lstat(path)
    return stat.S_IFMT(st.st_mode)

def compare_directory_structure(dir1, dir2, parent_dir='', ret=None, include_possibly_modified=False):
    if ret is None:
        ret = {
            'removed_dirs': [],
            'added_dirs': [],
            'removed_files': [],
            'added_files': []
        }

        if include_possibly_modified:
            ret['possibly_modified'] = []


    dir1_contents = set(os.listdir(dir1)) if dir1 else set()
    dir2_contents = set(os.listdir(dir2))

    removed = dir1_contents - dir2_contents
    added = dir2_contents - dir1_contents


    # those present in both directories must have the same type and link destinations
    for fname in dir2_contents:
        if not fname in dir1_contents:
            continue

        path = os.path.join(parent_dir, fname)
        diskpath1 = os.path.join(dir1, fname)
        diskpath2 = os.path.join(dir2, fname)

        if get_file_type(diskpath1) != get_file_type(diskpath2) or (os.path.islink(diskpath1) and os.readlink(diskpath1) != os.readlink(diskpath2)):
            added.add(fname)
            removed.add(fname)

    for removed_dir in removed:
        diskpath = os.path.join(dir1, removed_dir)
        path = os.path.join(parent_dir, removed_dir)

        if not os.path.islink(diskpath) and os.path.isdir(diskpath):
            ret['removed_dirs'].append(path)
        else:
            ret['removed_files'].append(path)

    for added_dir in added:
        diskpath = os.path.join(dir2, added_dir)
        path = os.path.join(parent_dir, added_dir)

        if not os.path.islink(diskpath) and os.path.isdir(diskpath):
            ret['added_dirs'].append(path)
        else:
            ret['added_files'].append(path)

    common_set = (dir1_contents & dir2_contents) - added # need added so that those detected earlier are filtered
    for fname in common_set:
        path = os.path.join(parent_dir, fname)

        diskpath1 = os.path.join(dir1, fname)
        diskpath2 = os.path.join(dir2, fname)

        d1stat = os.lstat(diskpath1)

        if stat.S_ISREG(d1stat.st_mode): # then d2 must also be a regular file, we've checked that earlier
            ret['possibly_modified'].append(path)

    for subdir in dir2_contents:
        subdir_path = os.path.join(dir2, subdir)
        if os.path.isdir(subdir_path) and not os.path.islink(subdir_path):
            new_dir1 = dir1 and os.path.join(dir1, subdir)
            new_dir1 = new_dir1 if (dir1 and os.path.isdir(new_dir1) and not os.path.islink(new_dir1)) else None

            compare_directory_structure(
                new_dir1, subdir_path, os.path.join(parent_dir, subdir), ret, include_possibly_modified=include_possibly_modified
            )

    return ret


def compare_directories(dir1, dir2):
    # note excludes can be used
    dir1 = dir1.rstrip('/')
    dir2 = dir2.rstrip('/')

    structure_diff = compare_directory_structure(dir1, dir2, include_possibly_modified=True)

    changed_files = retrieve_changed_files(dir1, dir2, sorted(structure_diff['possibly_modified']))


    ret = {
        'remove_dirs': structure_diff['removed_dirs'],
        'make_dirs': structure_diff['added_dirs'],
        'remove_files': structure_diff['removed_files'],
        'make_symlinks': [],
        'binary_files': changed_files['binary_files'], # ??
        'new_binary_files': [],
        'other_diff_lines': []
    }


    new_text_files = []


    for new_file in structure_diff['added_files']:
        diskpath = os.path.join(dir2, new_file)
        dstat = os.lstat(diskpath)

        if stat.S_ISLNK(dstat.st_mode):
            ret['make_symlinks'].append((new_file, os.readlink(diskpath)))
        
        elif stat.S_ISREG(dstat.st_mode): # then d2 must also be a regular file, we've checked that earlier
            if is_file_binary(diskpath):
                ret['new_binary_files'].append(new_file)
            else:
                new_text_files.append(new_file)
                

    for fname in sorted(changed_files['text_files'] + new_text_files):
        diff_process = subprocess.run(['diff', '-Nu', f'{dir1}/{fname}', f'{dir2}/{fname}'], capture_output=True) # -N so that new files work
        diff_output = diff_process.stdout.decode()

        if diff_process.returncode == 2:
            print(diff_output, file=sys.stderr)
            raise Exception("An error occurred while running the diff command.")

        in_prolog = True

        ret['other_diff_lines'].append(f'diff -rNu a/{fname} b/{fname}') # donno how secure and exact it is

        for line in diff_output.splitlines():
            if not line:
                continue

            if line[0] not in ('d', ' ', '-', '+', '@', '\\'):
                continue

            if in_prolog:
                if line.startswith('--- '):
                    lparts = line.split(' ')
                    lparts[1] = 'a' + lparts[1][len(dir1):]
                    line = ' '.join(lparts)
        
                elif line.startswith('+++ '):
                    lparts = line.split(' ')
                    lparts[1] = 'b' + lparts[1][len(dir2):]
                    line = ' '.join(lparts)
        
                    in_prolog = False
        
        
            ret['other_diff_lines'].append(line)

   
    return ret


def diff_command(dir1, dir2, write_line):
    dir1 = dir1.rstrip('/') or '/.'
    dir2 = dir2.rstrip('/') or '/.'


    diff_dict = compare_directories(dir1, dir2)
    if diff_dict['remove_dirs']:
        write_line('remove directories:')
        
        for fname in diff_dict['remove_dirs']:
            write_line(f'  {escape_text_line(fname)}')

        write_line('')

    if diff_dict['make_dirs']:
        write_line('create directories:')
        
        for fname in diff_dict['make_dirs']:
            write_line(f'  {escape_text_line(fname)}')

        write_line('')


    if diff_dict['remove_files']:
        write_line('remove:')
        
        for fname in diff_dict['remove_files']:
            write_line(f'  {escape_text_line(fname)}')

        write_line('')

    if diff_dict['make_symlinks']:
        write_line('create symlinks:')
        
        for fname, target in diff_dict['make_symlinks']:
            write_line(f'  {escape_text_line(fname)}')
            write_line(f'    {escape_text_line(target)}')
            write_line('')

        write_line('')

    if diff_dict['binary_files']:
        write_line('bindiff:')
        file_groups = [(f'{dir1}/{fname}', f'{dir2}/{fname}', fname) for fname in diff_dict['binary_files']]

        bindiff_diff('.', file_groups, with_indent(write_line))
        write_line('')

    if diff_dict['new_binary_files']:
        write_line('new binary files:')
        for fname in diff_dict['new_binary_files']:
            write_line(f'  {escape_text_line(fname)}')

            with open(f'{dir2}/{fname}', 'rb') as f:
                fcontent = base64.b64encode(gzip.compress(f.read())).decode()
                
                for x in range(0, len(fcontent), 120):
                    write_line(f'    {fcontent[x:x+120]}')

            write_line(f'  ')

    if diff_dict['other_diff_lines']:
        write_line('diff:')

        for line in diff_dict['other_diff_lines']:
            write_line(f'  {escape_text_line(line, allow_spaces=True)}')
        
        write_line('')



def parse_diff(reader):
    ret = {
        'remove_dirs': [],
        'make_dirs': [],
        'remove_files': [],
        'make_symlinks': [],
        'bindiff': None,
        'new_files': [],
        'diff': None
    }

    for line in reader.get_lines():
        if not line:
            continue
    
        if line == 'remove directories:':
            for fname in reader.get_lines():
                ret['remove_dirs'].append(unescape_text_line(fname))

        elif line == 'create directories:':
            for fname in reader.get_lines():
                ret['make_dirs'].append(unescape_text_line(fname))

        elif line == 'remove:':
            for fname in reader.get_lines():
                ret['remove_files'].append(unescape_text_line(fname))

        elif line == 'create symlinks:':
            for fname in reader.get_lines():
                ret['make_symlinks'].append((unescape_text_line(fname), unescape_text_line(list(reader.get_lines())[0])))

        elif line == 'bindiff:':
            ret['bindiff'] = bindiff_parse_diff(reader)
    
        elif line == 'new binary files:':
            for fname in reader.get_lines():
                fcontent = ''.join(unescape_text_line(l) for l in reader.get_lines())
                ret['new_files'].append((unescape_text_line(fname), base64.b64decode(fcontent)))
    
        elif line == 'diff:':
            ret['diff'] = ('\n'.join(unescape_text_line(l) for l in reader.get_lines()) + '\n').encode('utf-8')

    return ret

def verify_patches(patches, dir):
    # this function verifies whether a given patch can be applied
    # without applying it
    """
    patches = {
        'remove_dirs': [],
        'make_dirs': [],
        'remove_files': [],
        'make_symlinks': [],
        'bindiff': None,
        'new_files': [],
        'diff': None
    }

    """
    
    for fname in chain(patches['remove_dirs'], patches['remove_files']):
        fpath = f'{dir}/{fname}'
        if not os.access(fpath, os.W_OK):
            raise ValueError(f'{fpath}: not removable.')

    def get_last_existing_parent(path):
        while path and not os.path.exists(path):
            path = os.path.dirname(path)

        return path

    for dirname in patches['make_dirs']:
        dirpath = os.path.join(dir, dirname)
        parent_dir = get_last_existing_parent(dirpath)
        if not os.access(parent_dir, os.W_OK):
            raise ValueError(f'{parent_dir}: not writable.')

    for symlink in patches['make_symlinks']:
        symlink_path = os.path.join(dir, symlink[0])
        parent_dir = get_last_existing_parent(symlink_path)
        if not os.access(parent_dir, os.W_OK):
            raise ValueError(f'{parent_dir}: not writable.')

    for new_file, _ in patches['new_files']:
        file_path = os.path.join(dir, new_file)
        parent_dir = get_last_existing_parent(file_path)
        if not os.access(parent_dir, os.W_OK):
            raise ValueError(f'{parent_dir}: not writable.')

    if patches['bindiff']:
        bindiff_verify(patches['bindiff'], dir)

    if patches['diff']:
        patch_process = subprocess.Popen(['patch', '-d', dir, '--dry-run', '-p1'], 
                                            stdin=subprocess.PIPE)

        patch_process.stdin.write(patches['diff'])
        patch_process.stdin.close()

        patch_process.wait()

        if patch_process.returncode:
            raise ValueError(f'patch command error.')



def apply_patches(patches, dir):
    """
    patches = {
        'remove_dirs': [],
        'make_dirs': [],
        'remove_files': [],
        'make_symlinks': [],
        'bindiff': None,
        'new_files': [],
        'diff': None
    }
    
    """
    for dirname in patches['remove_dirs']:
        dirname = f'{dir}/{dirname}'
        shutil.rmtree(dirname)

    for fname in patches['remove_files']:
        fname = f'{dir}/{fname}'
        os.remove(fname)

    for dirname in patches['make_dirs']:
        dirname = f'{dir}/{dirname}'
        os.mkdir(dirname)

    for symlink, target in patches['make_symlinks']:
        symlink = f'{dir}/{symlink}'
        os.symlink(target, symlink)

    for fname, fcontent in patches['new_files']:
        fname = f'{dir}/{fname}'
        fcontent = gzip.decompress(fcontent)

        with open(fname, 'wb') as wf:
            wf.write(fcontent)

    if patches['bindiff']:
        bindiff_apply(patches['bindiff'], dir)
    
    if patches['diff']:
        patch_process = subprocess.Popen(['patch', '-d', dir, '-p1'], 
                                            stdin=subprocess.PIPE)
    
        patch_process.stdin.write(patches['diff'])
        patch_process.stdin.close()
    
        patch_process.wait()
    
        if patch_process.returncode:
            raise ValueError(f'patch command error.')
    
    


def patch_command(reader, diff_dir='.'):
    patches = parse_diff(reader)
    #pprint(patches)
    #exit()

    verify_patches(patches, diff_dir)
    apply_patches(patches, diff_dir)





def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command', required=True)

    diff_parser = subparsers.add_parser('diff')
    diff_parser.add_argument('dir1')
    diff_parser.add_argument('dir2')

    patch_parser = subparsers.add_parser('patch')
    patch_parser.add_argument('-d', '--patch-dir', default='.', help='Directory to apply patches')
    #patch_parser.add_argument('-u', '--unpatch', action='store_true', help='Experimental unpatch mode')

    args = parser.parse_args()
    print('args', args)

    if args.command == 'diff':
        diff_command(args.dir1, args.dir2, to_file())

    elif args.command == 'patch':
        patch_command(LineReader(sys.stdin), args.patch_dir)#, args.unpatch)

if __name__ == '__main__':
    main()
