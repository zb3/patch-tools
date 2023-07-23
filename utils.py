import re
import os
import sys
import io

# encoding note: text files should be opened with errors='surrogateescape' to handle invalid utf-8 in file names

def to_file(file=sys.stdout):
    def write(x):
        print(x, file=file)

    return write

def with_indent(stream, indent='  '):
    def write(x):
        for line in x.splitlines():
            stream(indent+line)

    return write 

class LineReader:
    def __init__(self, io, enable_comments=True):
        self.io = io
        self.line_to_unread = None
        self.enable_comments = enable_comments
        self.last_level = -1

    def next_line(self):
        if self.line_to_unread:
            l = self.line_to_unread
            self.line_to_unread = None
            return l

        line = self.io.readline()

        # we need pre-strip result
        if not line:
            return None
        
        return line.rstrip('\r\n')

    def unread_line(self, line):
        assert self.line_to_unread is None
        self.line_to_unread = line

    def get_lines(self, parent_indent=None, include_empty=False, include_comments=False):
        if parent_indent is None:
            parent_indent = self.last_level

        base_indent = None

        enable_comments = self.enable_comments and not include_comments
        in_multiline_comment = False

        while True:
            line = self.next_line()
            if line is None:
                break

            tline = line.strip()

            if not include_empty and not tline:
                continue

            # commented out lines don't require proper indentation
            if enable_comments:
                if in_multiline_comment:
                    if tline.startswith('*/'):
                        in_multiline_comment = False

                    continue

                if tline.startswith('#'):
                    continue

                if tline.startswith('/*'):
                    in_multiline_comment = True
                    continue

            line_indent = get_indent_level(line)

            if line_indent <= parent_indent:
                # in this case empty lines are simply ignored
                # but they're forwarded when there's no base indent so it's consistent with other levels of indent
                if not line:
                    continue

                self.unread_line(line)
                break

            if base_indent is None:
                base_indent = get_indent_level(line)

            self.last_level = base_indent
            yield line[base_indent:]


def get_indent_level(line: str) -> int:
    """
    Returns the number of leading spaces in a line, indicating its indentation level.
    """
    matches = re.match(r'^(\s+)', line)
    if matches:
        return len(matches.group(1))
    return 0

if __name__ == '__main__':
    data = """    o
    a
      b
      c
    d
    e
    """
    
    reader = LineReader(io.StringIO(data))
    
    for l in reader.get_lines(-1):
        print(f'l({l})')
    
        if l == 'a':
            for li in reader.get_lines():
                print('li', li)



def compare_directory_structure(dir1, dir2, parent_dir=''):
    removed_dirs = []
    added_dirs = []

    dir1_contents = set(os.listdir(dir1)) if dir1 else set()
    dir2_contents = set(os.listdir(dir2))

    removed = dir1_contents - dir2_contents
    for removed_dir in removed:
        if os.path.isdir(os.path.join(dir1, removed_dir)):
            removed_dirs.append(os.path.join(parent_dir, removed_dir))

    added = dir2_contents - dir1_contents
    for added_dir in added:
        if os.path.isdir(os.path.join(dir2, added_dir)):
            added_dirs.append(os.path.join(parent_dir, added_dir))


    for subdir in dir2_contents:
        subdir_path = os.path.join(dir2, subdir)
        if os.path.isdir(subdir_path):
            new_dir1 = dir1 and os.path.join(dir1, subdir)
            new_dir1 = new_dir1 if dir1 and os.path.isdir(new_dir1) else None

            nested_removed, nested_added = compare_directory_structure(
                new_dir1, subdir_path, os.path.join(parent_dir, subdir)
            )
            removed_dirs.extend(nested_removed)
            added_dirs.extend(nested_added)

    return removed_dirs, added_dirs

def escape_text_line(line, allow_spaces=False):
    line = line.replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r')
    if not allow_spaces and re.match(r'^\s', line):
        line = '\\' + line
    
    # assumes comments must start the line (spaces allowed)
    line = re.sub(r'^(\s*)(#|\/\*)', lambda m: f'{m.group(1)}\\{m.group(2)}', line)

    return line

def _unescape_text_replacement(m):
    x = m.group(1)
    if x == 'n':
        return '\n'
    elif x == 'r':
        return '\r'
    elif x == 't':
        return '\t'
    
    return x

def unescape_text_line(line):
    return re.sub(r'\\(.)', _unescape_text_replacement, line)

