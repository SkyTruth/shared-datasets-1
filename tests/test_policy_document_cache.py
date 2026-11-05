"""Parser reuse preserves edits, options, errors and caller-owned results."""
import io

import hcl2
import pytest
import yaml


def test_policy_caches_reuse_content_but_never_share_mutable_documents(parsed_policy_documents):
    yaml_cache, hcl_cache = parsed_policy_documents
    for cache in (yaml_cache, hcl_cache):
        cache.cache_clear()
    text = 'locals { values = ["original"] }'
    first = hcl2.loads(text)
    expected = hcl2.loads(text)
    first['locals'][0]['values'].append('mutated')
    assert hcl2.loads(text) == expected
    assert hcl_cache.cache_info().misses == 1
    assert hcl_cache.cache_info().hits == 2
    options = hcl2.SerializationOptions(strip_string_quotes=True)
    assert hcl2.loads(text, serialization_options=options) != expected
    assert hcl2.loads(text.replace('original', 'changed'), serialization_options=options) != hcl2.loads(text, serialization_options=options)
    options.strip_string_quotes = False
    assert hcl2.loads(text, serialization_options=options) == expected
    first = yaml.safe_load('values: [original]')
    first['values'].append('mutated')
    assert yaml.safe_load('values: [original]') == {'values': ['original']}
    assert yaml_cache.cache_info().misses == 1 and yaml_cache.cache_info().hits == 1
    assert yaml.safe_load('values: [changed]') == {'values': ['changed']}
    assert yaml.safe_load(io.StringIO('values: [stream]')) == {'values': ['stream']}
    assert yaml.safe_load(b'values: [bytes]') == {'values': ['bytes']}


@pytest.mark.parametrize('module,name,text', [(yaml, 'safe_load', 'values: ['), (hcl2, 'loads', 'locals { values = [')])
def test_policy_cache_keeps_real_parser_errors(module, name, text):
    for _ in range(2):
        with pytest.raises(Exception):
            getattr(module, name)(text)
