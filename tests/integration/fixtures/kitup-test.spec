Name:           kitup-test
Version:        1.0
Release:        1
Summary:        Fixture package for kitup integration tests
License:        MIT
BuildArch:      noarch

%description
A tiny package used by the kitup integration test suite.

%prep

%build

%install
mkdir -p %{buildroot}/usr/share/kitup-test
printf 'kitup integration fixture\n' > %{buildroot}/usr/share/kitup-test/marker

%files
%dir /usr/share/kitup-test
/usr/share/kitup-test/marker

%changelog
* Sat Oct 24 2026 kitup tests <kitup@example.invalid> - 1.0-1
- Initial fixture package.
