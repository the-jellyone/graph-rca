"""Optional language output only. Providers never receive executable tools."""
import json
import os
import re
from pathlib import Path
import requests


class ProviderError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__('provider request failed')


def load_environment(path):
    # Match the simple KEY=value files used by this project's chat configuration.
    # No expansion, evaluation, includes or secret values in diagnostics.
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, value = line.split('=', 1)
        name, value = name.strip(), value.strip()
        if name not in {'LLM_PROVIDER', 'GEMINI_API_KEY', 'GEMINI_MODEL', 'GROQ_API_KEY', 'GROQ_MODEL', 'LIVE_NARRATION_MODE'}:
            continue
        if value[:1] in ('\"', "'") and value[-1:] == value[:1]:
            value = value[1:-1]
        else:
            value = value.split(' #', 1)[0].rstrip()
        os.environ.setdefault(name, value)


def post_json(url, headers, payload):
    # Stream a bounded provider response. Status errors never log response bodies or headers.
    with requests.post(url, headers=headers, json=payload, timeout=(5, 20), stream=True) as response:
        if response.status_code != 200:
            raise ProviderError(response.status_code)
        data = bytearray()
        for chunk in response.iter_content(4096):
            data.extend(chunk)
            if len(data) > 65536:
                raise ValueError('provider response exceeded bound')
        return json.loads(data)


def summary(receipt):
    decision = receipt.get('diagnosis')
    overall=''
    if 'overall_latency_seconds' in receipt:
        outcome='verified recovery' if receipt.get('health',{}).get('confirmed') else 'the final recorded outcome'
        overall=f" The overall response took {receipt['overall_latency_seconds']:.1f} seconds from the injection request to {outcome}."
    if not decision:
        return f"Investigation {receipt.get('id','unknown')}: {receipt['status']}. No diagnosis was completed.{overall}"
    recovery = 'Agent repair and health checks passed.' if receipt.get('resolved') else 'No agent repair was confirmed.'
    if receipt.get('healthy_control_confirmed'):
        recovery = 'Healthy control confirmed by checkout and resource checks.'
    elif receipt.get('case')=='healthy' and receipt.get('health',{}).get('confirmed'):
        recovery = 'No fault was injected. Functional health checks passed; the agent did not confirm a healthy classification.'
    elif receipt.get('cleanup_confirmed') and not receipt.get('resolved'):
        recovery += ' The injector cleaned up its own fault, and the lab passed health checks.'
    timing = ''
    if 'diagnosis_seconds' in receipt:
        origin='after the injection request' if receipt.get('latency_origin')=='injection_requested' else 'after injection completed'
        timing = f" Diagnosis took {receipt['diagnosis_seconds']:.1f} seconds {origin}."
        if 'verified_recovery_seconds' in receipt:
            timing += f" Recovery checks finished at {receipt['verified_recovery_seconds']:.1f} seconds."
    return f"Status {receipt['status']}. Agent diagnosis: {decision['prediction']}, confidence {decision['confidence']:.1%}. {recovery}{timing}{overall}"


class Narrator:
    def __init__(self, env_file=None, enabled=True):
        if env_file:
            load_environment(env_file)
        self.provider = os.getenv('LLM_PROVIDER', 'gemini').lower()
        self.key = os.getenv('GEMINI_API_KEY' if self.provider == 'gemini' else 'GROQ_API_KEY', '')
        self.enabled = enabled and os.getenv('LIVE_NARRATION_MODE', 'local') == 'external' and bool(self.key) and not self.key.startswith('your_')

    def describe(self, receipt):
        authoritative = summary(receipt)
        if not self.enabled:
            return {'mode': 'deterministic', 'authoritative': authoritative, 'commentary': None}
        # No raw log text, injector labels, keys, shell requests or tool access.
        decision = receipt.get('diagnosis')
        facts = {'diagnosis': {name: decision[name] for name in ('prediction','confidence')} if decision else None,
                 'status': receipt['status'],
                 'agent_repair': receipt.get('agent_repair', False), 'resolved': receipt.get('resolved', False),
                 'cleanup_confirmed': receipt.get('cleanup_confirmed', False),
                 'health_confirmed': receipt.get('health', {}).get('confirmed', False)}
        facts['lab_restored']=bool(facts['cleanup_confirmed'] and facts['health_confirmed'])
        facts['timing_seconds']={key:receipt[key] for key in ('diagnosis_seconds','repair_command_completed_seconds',
            'overall_latency_seconds') if key in receipt}
        facts['timing_origin']=receipt.get('latency_origin','injection_completed')
        prompt = ('Explain these controller facts in at most four simple sentences. Include the overall response time when provided, rounded to one decimal place. '
            'Do not diagnose again or recommend commands. resolved means AGENT repair only. '
            'When lab_restored is true, the lab is healthy, including when injector cleanup restored it. '
            'Do not call the fault unresolved just because resolved is false. '
            'Claim an agent repair only when resolved is true.\n') + json.dumps(facts)
        try:
            if self.provider == 'gemini':
                model = os.getenv('GEMINI_MODEL', 'gemini-2.0-flash')
                if not re.fullmatch(r'[A-Za-z0-9_.-]+', model):
                    raise ValueError('invalid Gemini model name')
                response = post_json(f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
                    {'x-goog-api-key': self.key}, {'contents': [{'parts': [{'text': prompt}]}],
                    'generationConfig': {'maxOutputTokens': 200, 'temperature': 0}})
                text = response['candidates'][0]['content']['parts'][0]['text']
            elif self.provider == 'groq':
                response = post_json('https://api.groq.com/openai/v1/chat/completions',
                    {'Authorization': 'Bearer ' + self.key}, {'model': os.getenv('GROQ_MODEL', 'llama-3.1-8b-instant'),
                    'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': 1000, 'temperature': 0})
                text = response['choices'][0]['message']['content']
            else:
                raise ValueError('unsupported provider')
            text = re.sub(r'[\x00-\x1f\x7f]', ' ', str(text))[:1500]
            if not text.strip():
                raise ValueError('provider returned no narration')
            return {'mode': self.provider, 'authoritative': authoritative, 'commentary': text}
        except Exception as exc:
            # Provider errors can contain credentials or HTTP headers. Save only their type.
            result = {'mode': 'deterministic_fallback', 'authoritative': authoritative,
                    'commentary': None, 'provider_error_type': type(exc).__name__}
            if isinstance(exc, ProviderError):
                result['provider_http_status'] = exc.status
            return result
