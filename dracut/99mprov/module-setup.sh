#!/bin/bash

check () {
  return 0
}

depends () {
  return 0
}

install () {
  inst_multiple /sbin/load_policy /sbin/setsebool \
	/bin/date /etc/sysconfig/mprov /usr/sbin/setenforce \
	/usr/sbin/sulogin /usr/sbin/pivot_root /usr/sbin/chroot \
	/usr/bin/clear /usr/bin/rsync /lib64/libacl.so.1 /lib64/libpopt.so.0 \
	/lib64/libc.so.6 /lib64/libattr.so.1 /usr/bin/unshare \
	/lib64/libselinux.so.1 /lib64/libm.so.6 /lib64/libpcre.so.1 \
	/lib64/libdl.so.2 /lib64/libpthread.so.0 /usr/bin/find

    
    $moddir/python-deps /usr/bin/mprov /usr/lib64/python2.7/_sysconfigdata.py | while read dep; do
        case "$dep" in
            *.so) inst_library $dep ;;
            *.py) inst_simple $dep ;;
            *) inst $dep ;;
        esac
    done

  #inst $moddir/linuxrc.switchroot /linuxrc.switchroot
  inst $moddir/linuxrc /linuxrc
  ln_r /linuxrc "/init"
  ln_r /linuxrc "/sbin/init"
  #inst_hook pre-mount 1 $moddir/linuxrc.sh
}



