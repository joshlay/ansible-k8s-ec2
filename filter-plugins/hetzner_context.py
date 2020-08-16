import toml
from ansible.errors import AnsibleError

class FilterModule(object):
    def filters(self):
        return {'hetzner_token': self.return_token}
    def return_token(self, raw_toml, context='active'):
        toml_doc = toml.loads(raw_toml)
        if context == 'active':
            context = toml_doc['active_context']
        token_list = [contx['token'] for contx in toml_doc['contexts'] if contx['name'] == context]
        if len(token_list) == 0:
            raise AnsibleError(f'Unable to get API key for {context}')
        token = token_list[0]
        return token

