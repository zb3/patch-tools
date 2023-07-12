
# patch-tools

Currently bindiff.

## Bindiff

The goal of this tool is to make it possible to distribute patches to (possibly multiple) binaries at a given path in a human-readable format that supports comments (and can be audited).

### Rationale
Say that there's an app called Lemodyne Studio which let's you pretend you're a bad singer (because you always hit every note perfectly and this doesn't sound so natural). It's not free, but it's easy to crack and you'd like to share this with as many singers as possible because you're super tired of constantly hearing only perfect vocal performances sounding like autotune (which you hate).

So you want to distribute that crack, however, the app has a 80MB+ dll file that needs to be patched. Currently there are few problems with that:
* if you wish to distribute the cracked binaries directly, you not only need to repack the installer but also need to find a host to host the whole file
* that kind of distribution might violate copyright laws, and we of course don't want to violate our favorite law :(
* the patch is not easy to audit, because it's not known what has been done to each file, additionally, the new installer seems completely opaque to the user, it's not known what it actually does
* since all this is so cumbersome, it's rational to assume that that host 'd not just host it for free, but potentially bundle some malware inside
* even if you wanted to make a classic binary crack program, the same security concerns still apply - it's not obvious what the program does and the binary needs to be hosted somewhere

What if everything you need to share to make Lemodyne Studio available to everyone was just this piece of code:
```diff
>> Program Files/Celenomy/Lemodyne 5/Lemodyne.exe

# disable bundle dll signature verification
@0x00007076
- 08 8d 7b f6
+ 00 48 31 ff

>> Program Files/Common Files/Celenomy/Bundles/LemodyneCore-5.3.1.018.dll
# disable wrapper exe signature verification
@0x00e354ac
- 07 02
+ 00 00
@0x00e356d4
- 32 c0
+ b0 01

# not a crack - this just reenables 256 samples buffer size
@0x0145bcd5
- 02
+ 01

# patch auth search - just return the first app
@0x014cdcc0
- e8 cb 09 00 00
+ b8 b5 00 7b fa

# but set its type to 0x16 (the full studio edition)
@0x014cdcdd
- 18
+ 16

# network patches not included, best to use something like unshare -Urn
/*
@0x01725bea
- 8b d7
+ 31 d2
*/
```
Wouldn't this be great? (I think I should win a nobel prize I mean I'm not bragging but ah just look at this...)

So... the advantages of this approach are clear:
* there's no need to create any custom installers or programs that apply the patch after installing
* the patch file is easy to read, it's obvious that it doesn't contain any malware because there are simply not enough bytes to introduce one
* the patch file contains comments, so it's even possible to let the user customize the patch (for example by removing undesired changes or adjusting some changed bytes)
* the patch file is small and text-only, so it's easy to find a place to host it for free
* the actual program can then be safely downloaded from the official website (no need to pay for traffic or violate (our favorite) copyright law :)

Okay... so how to use bindiff? Let me explain.

### Producing patches

Let's say that the files to be patched are in these locations:
```
.../Program Files/Celenomy/Lemodyne 5/Lemodyne.exe
.../Program Files/Common Files/Celenomy/Bundles/LemodyneCore-5.3.1.018.dll
```
This `...` part is system dependent, so it shouldn't be included in the patch. That's the base directory.

Now suppose you saved the original files with the `.bak` suffix. Then the example command to produce the patch would be:
```
# change to the base directory (or specify it in the -d diff subcommand argument)
cd ~/.wine/drive_c

python bindiff.py diff \
  'Program Files/Celenomy/Lemodyne 5/Lemodyne.exe.bak' 'Program Files/Celenomy/Lemodyne 5/Lemodyne.exe' -p \
  'Program Files/Common Files/Celenomy/Bundles/LemodyneCore-5.3.1.018.dll.bak' 'Program Files/Common Files/Celenomy/Bundles/LemodyneCore-5.3.1.018.dll' -p
```

That's because the `diff` subcommand expects one or more files that are specified by a group of 3 arguments:
```
[original file] [patched file] [which name to use -o means the original file is now at the correct the path, -p means the patched file is now at the correct path, you can also supply custom path to be used inside the patch]
```

The command then outputs the patch on the standard output. You can manually annotate it by adding lines starting with `#` which are then treated as comments.
You can also comment out multiple lines by using the `/*` and `*/` syntax.

### Applying them

If you have the patch file and the original files installed at the correct location, applying the patch is super easy:
```
# change to the base directory (or specify it in the -d patch subcommand argument)
cd ~/.wine/drive_c

python bindiff.py patch </the/patch/file.patch
```

The original bytes are checked for being correct, so the program will never overwrite bytes that were already modified by the user. This prevents data loss, but also makes it possible to provide the undo functionality.
Yes, you can undo the patch using the `-u` switch! (wow, what an impressing feature, isn't it?)

```
# change to the base directory
cd ~/.wine/drive_c

python bindiff.py patch -u </the/patch/file.patch
```

### bindiff FAQ:

#### Q: Are there any elephants in Antarctica?
**A:** Yes, but I'm the only one currently living there.

#### Q: Can you play piano?
**A:** I can play it like I can make it emit some noise, it's just that I can't tell which note will be played in advance.
