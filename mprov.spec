Name:		mprov 
Version:	0.1
Release:	%{?rel}%{?dist}
Summary:	Multi-PROVisioner.

License:	GPL
URL:		http://www.tulg.org/mprov/
Source0:	http://www.tulg.org/mprov/%{name}-%{version}.tar.gz

BuildRequires:	python-devel
Requires:	python, /usr/bin/nc
BuildArch:	noarch

%description
Multi-PROVisioner(mprov) is a system for handing root file system images that
need to be synced out to many, many machines.  A master node keeps track of
all of the images, the "worker" nodes grab copies from the master and actually
do the work of syncing out to clients.  It allows for a "branched" type of 
provisioning.

%prep
%setup -q -n mprov

%install
mkdir -p %{buildroot}/usr/bin/
cp mprov.py %{buildroot}/usr/bin/mprov
mkdir -p %{buildroot}/%{python2_sitelib}
cp -r mprov/ %{buildroot}/%{python2_sitelib}
mkdir -p %{buildroot}/usr/lib/systemd/system/
cp service-files/mprov-master.service %{buildroot}/usr/lib/systemd/system/
cp service-files/mprov-worker.service %{buildroot}/usr/lib/systemd/system/
mkdir -p %{buildroot}/etc/sysconfig/
cp service-files/mprov %{buildroot}/etc/sysconfig/
mkdir -p %{buildroot}/usr/sbin/
cp bin/mprovcmd %{buildroot}/usr/sbin/mprovcmd
mkdir -p %{buildroot}/usr/lib/firewalld/services/
cp mprov.xml %{buildroot}/usr/lib/firewalld/services/
mkdir -p %{buildroot}/usr/lib/dracut/modules.d/99mprov/
cp -r dracut/99mprov/linuxrc %{buildroot}/usr/lib/dracut/modules.d/99mprov/
cp -r dracut/99mprov/module-setup.sh %{buildroot}/usr/lib/dracut/modules.d/99mprov/
cp -r dracut/99mprov/python-deps %{buildroot}/usr/lib/dracut/modules.d/99mprov/
cp -r dracut/99-mprov.conf %{buildroot}/etc/dracut.conf.d/

%post
/usr/bin/systemctl daemon-reload

%postun
/usr/bin/systemctl daemon-reload

%files
/usr/bin/mprov
%{python2_sitelib}/mprov
/usr/lib/systemd/system/*
%config /etc/sysconfig/mprov
%config /etc/dracut.conf.d/99-mprov.conf
%attr(0700, root, root) /usr/sbin/mprovcmd
/usr/lib/firewalld/services/mprov.xml
/usr/lib/dracut/modules.d/99mprov


%changelog
* Mon Jul 02 2018 Jason Williams <jasonw@tulg.org>
- Adding dracut config file to exclude mprov unless explicitly included.

* Wed May 16 2018 Jason Williams <jasonw@tulg.org>
- Lots of modifications for bugs
- Added dracut module for including in initramfs

* Fri Nov 03 2017 Jason Williams <jasonw@tulg.org>
- reload of systemd after install/uninstall
- add new file for mprov cmd line wrapper

* Wed Nov 01 2017 Jason Williams <jasonw@tulg.org>
- fixed /etc/sysconfig/mprov to be a config file.

* Tue Aug 22 2017 Jason Williams <jasonw@tulg.org>
- Initial RPM release
