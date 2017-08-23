Name:		mprov 
Version:	0.1
Release:	%{?rel}%{?dist}
Summary:	Multi-PROVisioner.

License:	GPL
URL:		http://www.tulg.org/mprov/
Source0:	http://www.tulg.org/mprov/%{name}-%{version}.tar.gz

BuildRequires:	python-devel
Requires:	python
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

%files
/usr/bin/mprov
%{python2_sitelib}/mprov
/usr/lib/systemd/system/*
/etc/sysconfig/*



%changelog
* Tue Aug 22 2017 Jason Williams <jasonw@tulg.org>
- Initial RPM release
