import os

import pytest

from kitup.errors import TaskFailed
from kitup.modules import files

from .helpers import template_context, write_source


def render(tmp_path, template_text, *, variables=None, vars=None, facts=None):
    source = tmp_path / "helper.j2"
    write_source(source, template_text)
    dest = tmp_path / "helper.out"
    template_context(tmp_path, vars=vars, facts=facts)
    files.template(source, dest, variables=variables)
    return dest.read_text()


def test_jinja_path_filters(tmp_path):
    assert render(tmp_path, "{{ '/foo/bar.txt' | basename }}") == "bar.txt"
    assert render(tmp_path, "{{ '/foo/bar/' | basename }}") == ""
    assert render(tmp_path, "{{ '/foo/bar.txt' | dirname }}") == "/foo"
    assert render(tmp_path, "{{ 'a/b.txt' | splitext | list }}") == "['a/b', '.txt']"
    assert render(tmp_path, "{{ 'a/../b' | normpath }}") == "b"
    assert render(tmp_path, "{{ '~' | expanduser }}") == os.path.expanduser("~")
    assert render(tmp_path, "{{ '/foo/bar.txt' | path_join }}") == "/foo/bar.txt"
    assert render(tmp_path, "{{ ['/foo', 'bar', 'baz.txt'] | path_join }}") == "/foo/bar/baz.txt"


def test_jinja_regex_filters(tmp_path):
    assert render(tmp_path, "{{ 'Foo foo' | regex_replace('foo', 'bar', ignorecase=True) }}") == "bar bar"
    assert render(tmp_path, "{{ 'a1b2' | regex_findall('[a-z]') | join(',') }}") == "a,b"
    assert render(tmp_path, "{{ 'foo42' | regex_search('([0-9]+)', '\\\\1') }}") == "['42']"
    assert render(tmp_path, "{{ 'foo42' | regex_search('([0-9]+)', '\\\\g<1>') }}") == "['42']"
    assert render(tmp_path, "{{ 'foo42' | regex_search('(?P<num>[0-9]+)', '\\\\g<num>') }}") == "['42']"
    assert render(tmp_path, "{{ 'foo' | regex_escape }}") == "foo"
    assert render(tmp_path, "{{ 'a.b' | regex_escape('posix_basic') }}") == "a\\.b"
    assert render(tmp_path, "{{ 'a+b' | regex_escape('posix_extended') }}") == "a\\+b"


def test_jinja_string_filters(tmp_path):
    assert render(tmp_path, "{{ 'a,b,c' | split(',') | join('|') }}") == "a|b|c"
    assert render(tmp_path, "{{ 'a b' | quote }}") == "'a b'"
    assert render(tmp_path, "{{ none | quote }}") == "''"
    assert render(tmp_path, '{{ "it\'s" | squote }}') == "'it'\\''s'"
    assert render(tmp_path, "{{ none | squote }}") == "''"
    assert render(tmp_path, "{{ 'hello' | b64encode }}") == "aGVsbG8="
    assert render(tmp_path, "{{ 'aGVsbG8=' | b64decode }}") == "hello"
    assert render(tmp_path, "{{ '???' | b64encode(urlsafe=True) }}") == "Pz8_"
    assert render(tmp_path, "{{ 'Pz8_' | b64decode(urlsafe=True) }}") == "???"
    assert render(tmp_path, "{{ 'hello' | comment }}") == "# hello"
    assert render(tmp_path, "{{ ['a', 'b'] | comment }}") == "# a\n# b"
    assert render(tmp_path, "{{ 'hello' | comment('// ') }}") == "// hello"
    assert render(tmp_path, "{{ text | comment }}", variables={"text": "a\nb"}) == "# a\n# b"
    assert render(tmp_path, "{{ text | comment }}", variables={"text": "a\n\nb"}) == "# a\n#\n# b"


def test_jinja_data_filters(tmp_path):
    assert (
        render(tmp_path, "{{ {'a': {'b': 1}} | combine({'a': {'c': 2}}, recursive=True) | to_json(sort_keys=True) }}")
        == '{"a": {"b": 1, "c": 2}}'
    )
    assert render(tmp_path, "{{ [{'a': 1}, {'b': 2}] | combine | to_json(sort_keys=True) }}") == '{"a": 1, "b": 2}'
    assert (
        render(tmp_path, "{{ {'a': [1, 2]} | combine({'a': [2, 3]}, list_merge='append_rp') | to_json }}")
        == '{"a": [1, 2, 3]}'
    )
    assert render(tmp_path, "{{ [1, [2, [3]]] | flatten | join(',') }}") == "1,2,3"
    assert render(tmp_path, "{{ [1, [2, [3]]] | flatten(levels=1) | join(',') }}") == "1,2,[3]"
    assert render(tmp_path, "{{ [1, [2, [3]]] | flatten(levels='1') | join(',') }}") == "1,2,[3]"
    assert render(tmp_path, "{{ {'a': 1} | dict2items | to_json }}") == '[{"key": "a", "value": 1}]'
    assert render(tmp_path, "{{ [{'key': 'a', 'value': 1}] | items2dict | to_json }}") == '{"a": 1}'
    assert render(tmp_path, "{{ [1, 2] | union([2, 3]) | sort | join(',') }}") == "1,2,3"
    assert render(tmp_path, "{{ [1, 2] | intersect([2, 3]) | join(',') }}") == "2"
    assert render(tmp_path, "{{ [1, 2] | difference([2, 3]) | join(',') }}") == "1"
    assert render(tmp_path, "{{ [0, 2] | map('extract', {0: 'a', 2: 'c'}) | join(',') }}") == "a,c"
    assert render(tmp_path, "{{ [{'name': 'a', 'items': [1, 2]}] | subelements('items') | list | length }}") == "2"
    assert (
        render(tmp_path, "{{ [{'name': 'a', 'items': [1, 2]}] | subelements('items') | list | first | last }}") == "1"
    )


def test_jinja_datetime_and_sequence_filters(tmp_path):
    # Datetime filters.
    assert render(tmp_path, "{{ '2020-01-02 03:04:05' | to_datetime | strftime('%Y/%m/%d') }}") == "2020/01/02"
    assert render(tmp_path, "{{ '2020-01-02' | to_datetime('%Y-%m-%d') | strftime('%d.%m.%Y') }}") == "02.01.2020"

    # Sequence filters.
    assert render(tmp_path, "{{ [1, 2] | zip([3, 4]) | list | to_json }}") == "[[1, 3], [2, 4]]"
    assert render(tmp_path, "{{ [1] | zip_longest([2, 3]) | list | to_json }}") == "[[1, 2], [null, 3]]"
    assert render(tmp_path, "{{ [1] | zip_longest([2, 3], fillvalue=0) | list | to_json }}") == "[[1, 2], [0, 3]]"


def test_jinja_urlsplit_filter(tmp_path):
    url = "http://user:pass@example.com:8080/path?q=1#frag"
    assert render(tmp_path, f"{{{{ '{url}' | urlsplit('hostname') }}}}") == "example.com"
    assert render(tmp_path, f"{{{{ '{url}' | urlsplit('port') }}}}") == "8080"
    assert render(tmp_path, f"{{{{ ('{url}' | urlsplit).scheme }}}}") == "http"
    assert render(tmp_path, f"{{{{ ('{url}' | urlsplit)['path'] }}}}") == "/path"
    assert render(tmp_path, "{{ 'http://example.com/a;b?q=1' | urlsplit('path') }}") == "/a;b"


def test_jinja_serialization_filters(tmp_path):
    assert render(tmp_path, "{{ {'b': 2, 'a': 1} | to_json(sort_keys=True) }}") == '{"a": 1, "b": 2}'
    assert render(tmp_path, "{{ {'a': 1} | to_json(indent=2) }}") == '{\n  "a": 1\n}'
    assert render(tmp_path, "{{ '{\"a\": 1}' | from_json | to_json }}") == '{"a": 1}'
    assert render(tmp_path, "{{ {'a': [1, 2]} | to_yaml }}") == "a:\n- 1\n- 2\n"
    assert render(tmp_path, "{{ {'a': 1} | to_yaml(default_flow_style=True) }}") == "{a: 1}\n"
    assert render(tmp_path, "{{ {'a': 1} | to_yaml(default_flow_style=None) }}") == "{a: 1}\n"
    assert render(tmp_path, "{{ 'a: 1' | from_yaml | to_json }}") == '{"a": 1}'
    assert render(tmp_path, "{{ {'a': 1} | from_yaml | to_json }}") == '{"a": 1}'


def test_jinja_ipaddr_filter(tmp_path):
    assert render(tmp_path, "{{ '192.168.0.1' | ipaddr }}") == "192.168.0.1"
    assert render(tmp_path, "{{ '192.168.0.0/24' | ipaddr('network') }}") == "192.168.0.0"
    assert render(tmp_path, "{{ '192.168.0.0/24' | ipaddr('broadcast') }}") == "192.168.0.255"
    assert render(tmp_path, "{{ '192.168.0.0/24' | ipaddr('prefix') }}") == "24"
    assert render(tmp_path, "{{ '192.168.0.0/24' | ipaddr('netmask') }}") == "255.255.255.0"
    assert render(tmp_path, "{{ '192.168.0.1' | ipaddr('ipv4') }}") == "192.168.0.1"
    assert render(tmp_path, "{{ '192.168.0.0/24' | ipaddr('ipv4') }}") == "192.168.0.0/24"
    assert render(tmp_path, "{{ '2001:db8::1' | ipaddr('ipv6') }}") == "2001:db8::1"
    assert render(tmp_path, "{{ '2001:db8::/64' | ipaddr('ipv6') }}") == "2001:db8::/64"
    assert render(tmp_path, "{{ '192.168.0.1' | ipaddr('ipaddr') }}") == "192.168.0.1"
    assert render(tmp_path, "{{ 'not-an-ip' | ipaddr }}") == "False"
    assert render(tmp_path, "{{ '192.168.0.1' | ipaddr('ipv6') }}") == "False"


def test_jinja_ipmath_filter(tmp_path):
    assert render(tmp_path, "{{ '192.168.0.1' | ipmath(5) }}") == "192.168.0.6"
    assert render(tmp_path, "{{ '192.168.0.1' | ipmath(-1) }}") == "192.168.0.0"
    assert render(tmp_path, "{{ '192.168.0.0/24' | ipmath(5) }}") == "192.168.0.5"
    assert render(tmp_path, "{{ '192.168.0.0/24' | ipmath(-1) }}") == "192.168.0.255"
    assert render(tmp_path, "{{ '192.168.0.0/24' | ipmath('first_usable') }}") == "192.168.0.1"
    assert render(tmp_path, "{{ '192.168.0.0/24' | ipmath('last_usable') }}") == "192.168.0.254"
    assert render(tmp_path, "{{ '2001:db8::/126' | ipmath(2) }}") == "2001:db8::2"


def test_jinja_bool_filter(tmp_path):
    cases = [
        ("yes", "True"),
        ("ON", "True"),
        ("true", "True"),
        ("1", "True"),
        ("no", "False"),
        ("off", "False"),
        ("FALSE", "False"),
        ("0", "False"),
    ]
    for value, expected in cases:
        assert render(tmp_path, f"{{{{ '{value}' | bool }}}}") == expected, f"bool({value!r})"


def test_jinja_version_test(tmp_path):
    assert render(tmp_path, "{% if '1.2.3' is version('1.2.3') %}yes{% endif %}") == "yes"
    assert render(tmp_path, "{% if '1.10' is version('1.9', '>') %}yes{% endif %}") == "yes"
    assert render(tmp_path, "{% if '1.2' is version_compare('1.2.0', '<') %}yes{% endif %}") == "yes"
    assert (
        render(tmp_path, "{% if facts.version_id is version('40', '>=') %}yes{% endif %}", facts={"version_id": "40"})
        == "yes"
    )
    assert render(tmp_path, "{% if '1.0' is version('1.0', '!=') %}no{% else %}yes{% endif %}") == "yes"
    assert render(tmp_path, "{% if '1.0A' is version('1.0a', '<') %}yes{% endif %}") == "yes"

    operators = [
        ("1.0", "==", "1.0"),
        ("1.0", "=", "1.0"),
        ("1.0", "eq", "1.0"),
        ("1.0", "<", "1.1"),
        ("1.0", "lt", "1.1"),
        ("1.0", "<=", "1.0"),
        ("1.0", "le", "1.0"),
        ("1.1", ">", "1.0"),
        ("1.1", "gt", "1.0"),
        ("1.1", ">=", "1.1"),
        ("1.1", "ge", "1.1"),
        ("1.0", "!=", "1.1"),
        ("1.0", "<>", "1.1"),
        ("1.0", "ne", "1.1"),
    ]
    for left, operator, right in operators:
        template = f"{{% if '{left}' is version('{right}', '{operator}') %}}yes{{% endif %}}"
        assert render(tmp_path, template) == "yes", f"{left} {operator} {right}"


def test_jinja_regex_tests(tmp_path):
    assert render(tmp_path, "{% if 'foobar' is search('bar') %}yes{% endif %}") == "yes"
    assert render(tmp_path, "{% if 'foobar' is match('foo') %}yes{% endif %}") == "yes"
    assert render(tmp_path, "{% if 'foobar' is regex('bar', match_type='search') %}yes{% endif %}") == "yes"
    assert render(tmp_path, "{% if 'FOO' is search('foo', ignorecase=True) %}yes{% endif %}") == "yes"
    assert render(tmp_path, "{% if 'xfoobar' is match('foo') %}no{% else %}yes{% endif %}") == "yes"


def test_jinja_collection_tests(tmp_path):
    assert render(tmp_path, "{% if [0, 1] is any %}yes{% endif %}") == "yes"
    assert render(tmp_path, "{% if [1, 2] is all %}yes{% endif %}") == "yes"
    assert render(tmp_path, "{% if ['a', 'b'] is contains('b') %}yes{% endif %}") == "yes"
    assert render(tmp_path, "{% if ['a', 'b'] is contains('c') %}no{% else %}yes{% endif %}") == "yes"


def test_jinja_uri_tests(tmp_path):
    assert render(tmp_path, "{% if 'http://example.com' is uri %}yes{% endif %}") == "yes"
    assert render(tmp_path, "{% if 'mailto:user@example.com' is uri %}yes{% endif %}") == "yes"
    assert render(tmp_path, "{% if 'mailto:user@example.com' is url %}no{% else %}yes{% endif %}") == "yes"
    assert render(tmp_path, "{% if 'file:///tmp/x' is url %}yes{% endif %}") == "yes"
    assert render(tmp_path, "{% if 'urn:isbn:123' is urn %}yes{% endif %}") == "yes"
    assert (
        render(tmp_path, "{% if 'http://example.com' is uri(schemes=['https']) %}no{% else %}yes{% endif %}") == "yes"
    )


def test_jinja_ip_tests(tmp_path):
    assert render(tmp_path, "{% if '192.168.0.1' is ipaddr %}yes{% endif %}") == "yes"
    assert render(tmp_path, "{% if '192.168.0.1' is ipv4 %}yes{% endif %}") == "yes"
    assert render(tmp_path, "{% if '192.168.0.1' is ipv6 %}no{% else %}yes{% endif %}") == "yes"
    assert render(tmp_path, "{% if 'not-an-ip' is ipaddr %}no{% else %}yes{% endif %}") == "yes"


def test_jinja_default_filters_and_tests_still_work(tmp_path):
    assert render(tmp_path, "{{ ['a', 'b'] | join('-') }}") == "a-b"
    assert render(tmp_path, "{{ 'yes' | default('no') }}") == "yes"
    assert render(tmp_path, "{{ {'a': 1} | tojson }}") == '{"a": 1}'
    assert render(tmp_path, "{% if missing is defined %}no{% else %}yes{% endif %}") == "yes"
    assert render(tmp_path, "{% if true is boolean %}yes{% endif %}") == "yes"
    assert render(tmp_path, "{% if 'a' in ['a'] %}yes{% endif %}") == "yes"


def test_jinja_helper_errors_are_task_failures(tmp_path):
    templates = [
        "{{ 'maybe' | bool }}",
        "{{ {'a': 1} | combine({'b': 2}, list_merge='bogus') }}",
        "{% if '1' is version('2', version_type='semver') %}x{% endif %}",
        "{% if '1' is version('2', strict=True) %}x{% endif %}",
        "{{ '192.168.0.1' | ipaddr('bogus') }}",
        "{{ '192.168.0.0/24' | ipmath(999) }}",
        "{{ '192.168.0.1' | ipmath(2.9) }}",
        "{{ '[' | from_json }}",
        "{{ 'a: [' | from_yaml }}",
        "{{ 'not-a-date' | to_datetime }}",
        "{{ 'http://example.com' | urlsplit('bogus') }}",
    ]
    for template_text in templates:
        try:
            render(tmp_path, template_text)
        except TaskFailed as exc:
            assert "template rendering error" in str(exc), template_text
        else:
            pytest.fail(f"expected a template failure for {template_text!r}")
