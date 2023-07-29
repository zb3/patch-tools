import argparse
import re
import subprocess
import os
import sys
import shutil
import tempfile
import glob

from dirdiff import compare_directory_structure, retrieve_changed_files, diff_command as dirdiff_diff, parse_diff as dirdiff_parse, verify_patches as dirdiff_verify, apply_patches as dirdiff_apply
from utils import LineReader, to_file, with_indent
from xml_sort_utils import sort_attrs_xml


# currently not meant to be reversible as dirdiff is not reversible


def find_smali_files(srcdir):
  smali_files = []
  for dir in os.listdir(srcdir):
    if dir == 'smali' or re.match(r'^smali_classes\d+$', dir):
      for root, _, files in os.walk(os.path.join(srcdir, dir)):
        for file in files:
          if file.endswith('.smali'):
              smali_files.append(os.path.join(root, file))
                    
  return smali_files


def sort_do_not_compress_items(filename): # currently only for toplevel arrays
  sorting = False
  items = []
  
  newlines = []

  with open(filename, 'r') as file:
    for line in file:
      line = line.rstrip('\r\n')
      
      if line == 'doNotCompress:':
        sorting = True
        newlines.append(line)
          
      elif sorting and not line.startswith('-'):
        sorting = False
        items.sort()
        for item in items:
          newlines.append(f'- {item}')
                                
        newlines.append(line)
          
      elif sorting:
        items.append(line[2:])

  with open(filename, 'w') as wf:
    wf.write('\n'.join(newlines))


# while in manifest.mf we do remove some information apk can rely on
# it's not really different from relying on classes.dex signature
def normalize_unpacked_apk_dir(srcdir):
  # some debug info might be lost, but this should be semantically equivalent
  # like a canonical form

  smali_files = find_smali_files(srcdir)

  # pass 10 files to the sed command each time
  SED_CHUNK = 10
  for x in range(0, len(smali_files), SED_CHUNK):
    smali_batch = smali_files[x:x+SED_CHUNK]

    # --no-debug-info doesn't strip .source, yet other patching tools might do it
    subprocess.run(["sed", "-i", "-E", r"/^\s*\.(source) /d", *smali_batch], stdout=sys.stderr, check=True)

    # initial field values might sometimes be included, but are redundant
    subprocess.run(["sed", "-i", "-E", r"/^\.field/s/ = (false|null|0)$//", *smali_batch], stdout=sys.stderr, check=True)


  # currently we only modify this one file, but this might be more widely present
  if os.path.exists(f'{srcdir}/res/values/attrs.xml'):
    with open(f'{srcdir}/res/values/attrs.xml', 'r') as f:
      attrs_xml_string = f.read()

    attrs_xml_string = sort_attrs_xml(attrs_xml_string)

    with open(f'{srcdir}/res/values/attrs.xml', 'w') as wf:
      wf.write(attrs_xml_string)      

  to_remove = [
    'unknown/stamp-cert-sha256',
    'original/stamp-cert-sha256',

    # now, while manifest.mf can contain other information
    # it's not really different from relying on classes.dex signature
    'original/META-INF/MANIFEST.MF'
  ]

  for path in to_remove:
    try:
      os.remove(f'{srcdir}/{path}')
    except FileNotFoundError:
      pass


  sf_files = glob.glob(f'{srcdir}/original/META-INF/*.SF')
  rsa_files = glob.glob(f'{srcdir}/original/META-INF/*.RSA')

  if len(sf_files) == 1 and len(rsa_files) == 1:
    os.remove(sf_files[0])
    os.remove(rsa_files[0])

  # .version files are sometimes removed too
  for path in glob.glob(f'{srcdir}/original/META-INF/*.version'):
    os.remove(path)

  subprocess.run(['sed', '-i', 's/^apkFileName:.*/apkFileName: apk.apk/', f'{srcdir}/apktool.yml'])
  subprocess.run(['sed', '-i', r'/\s*stamp-cert-sha256:/d', f'{srcdir}/apktool.yml'])
  subprocess.run(['sed', '-i', r'/^- META-INF\/.*\.version$/d', f'{srcdir}/apktool.yml'])

  sort_do_not_compress_items(f'{srcdir}/apktool.yml')
  

def diff_command(apk1, apk2, write_line):
    apk1_dir = os.path.join(tempfile.gettempdir(), os.path.splitext(os.path.basename(apk1))[0]+'.extracted')
    apk2_dir = os.path.join(tempfile.gettempdir(), os.path.splitext(os.path.basename(apk2))[0]+'.extracted')

    # temporary dirs will be created in this directory
    
    shutil.rmtree(apk1_dir, ignore_errors=True)
    shutil.rmtree(apk2_dir, ignore_errors=True)

    apk_open_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'apk/open'))

    # first try without unpacking code/resources
    open_args = ['--no-src', '--no-res']

    # waith, the directory must be nonexistent?
    subprocess.run([apk_open_path, *open_args, '-o', apk1_dir, apk1], check=True, stdout=sys.stderr)
    subprocess.run([apk_open_path, *open_args, '-o', apk2_dir, apk2], check=True, stdout=sys.stderr)

    structure_diff = compare_directory_structure(apk1_dir, apk2_dir, include_possibly_modified=True)

    changed_files = retrieve_changed_files(apk1_dir, apk2_dir, sorted(structure_diff['possibly_modified']))

    all_changed_files = []
    all_changed_dirs = []

    for k in ('removed_dirs', 'added_dirs'):
      all_changed_dirs.extend(structure_diff[k])
  
    for k in ('removed_files', 'added_files'):
      all_changed_files.extend(structure_diff[k])

    for k in ('binary_files', 'text_files'):
      all_changed_files.extend(changed_files[k])

    resources_changed = ('AndroidManifest.xml' in all_changed_files) or ('resources.arsc'in all_changed_files) or ('res' in all_changed_dirs) or any(p.startswith('res/') for p in all_changed_files)

    classes_changed = any(p.endswith('.dex') for p in all_changed_files)

    if classes_changed or resources_changed:
      # need to rebuild

      shutil.rmtree(apk1_dir)
      shutil.rmtree(apk2_dir)

      open_args = []
      if not classes_changed:
        open_args.append('--no-src')
      
      else:
        # patches usually strip debug info so it'd just spam the diff
        # so we strip it
        open_args.append('--no-debug-info')
        
      if not resources_changed:
        open_args.append('--no-res')

      # lateron: attempt partial decoding, like with --force-manifest?
      subprocess.run([apk_open_path, *open_args, '-o', apk1_dir, apk1], check=True, stdout=sys.stderr)
      subprocess.run([apk_open_path, *open_args, '-o', apk2_dir, apk2], check=True, stdout=sys.stderr)

    # now tweak some stuff
    normalize_unpacked_apk_dir(apk1_dir)
    normalize_unpacked_apk_dir(apk2_dir)

    status_line = ' '.join(['apk', *open_args])
    write_line(status_line)
    
    dirdiff_diff(apk1_dir, apk2_dir, with_indent(write_line), exclude_paths=['original/AndroidManifest.xml'])


def parse_diff(reader):
  ret = {
    'open_args': [],
    'dirdiff': None,
    'apk_dir': None
  }

  for line in reader.get_lines():
    if line.startswith('apk'):
      ret['open_args'] = line.split(' ')[1:]
      ret['dirdiff'] = dirdiff_parse(reader)
      break
  
  return ret


def prepare_patch_dir(patches, apk):
  apk_dir = os.path.join(tempfile.gettempdir(), os.path.splitext(os.path.basename(apk))[0]+'.patchdir')

  shutil.rmtree(apk_dir, ignore_errors=True)

  apk_open_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'apk/open'))
  
  subprocess.run([apk_open_path, *patches['open_args'], '-o', apk_dir, apk], check=True, stdout=sys.stderr)
  
  normalize_unpacked_apk_dir(apk_dir)

  return apk_dir
  

def verify_patches(patches, apk_dir):
  dirdiff_verify(patches['dirdiff'], apk_dir)


def apply_patches(patches, apk_dir, new_apk):
  dirdiff_apply(patches['dirdiff'], apk_dir)

  apk_pack_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'apk/pack'))
  subprocess.run([apk_pack_path, apk_dir, new_apk], check=True, stdout=sys.stderr)


def patch_command(reader, orig_apk, new_apk=None):
  if not new_apk:
    if orig_apk.endswith('.apk'):
      new_apk = orig_apk[:-3]+'patched.apk'

  patches = parse_diff(reader)

  patch_dir = prepare_patch_dir(patches, orig_apk)

  verify_patches(patches, patch_dir)
  apply_patches(patches, patch_dir, new_apk)

  # yes, this intentionally runs only if no errors occured
  shutil.rmtree(patch_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command', required=True)

    diff_parser = subparsers.add_parser('diff')
    diff_parser.add_argument('apk1')
    diff_parser.add_argument('apk2')

    patch_parser = subparsers.add_parser('patch')
    patch_parser.add_argument('original_apk', help='APK to patch')
    patch_parser.add_argument('new_apk', nargs='?', default=None, help='APK to create (original_apk.patched.apk by default)')

    #patch_parser.add_argument('-u', '--unpatch', action='store_true', help='Experimental unpatch mode')

    args = parser.parse_args()

    if args.command == 'diff':
        diff_command(args.apk1, args.apk2, to_file())

    elif args.command == 'patch':
        patch_command(LineReader(sys.stdin), args.original_apk, args.new_apk)

if __name__ == '__main__':
    main()
