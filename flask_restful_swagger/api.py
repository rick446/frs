import json
from contextlib import closing
from urllib2 import urlopen

import yaml
from jsonref import JsonRef
from flask import request
from flask_restful import Api

from . import docs
from .errors import SwaggerError
from .validators import DefaultValidatingDraft4Validator


class SwaggerApi(Api):
    TRUTHY = ('true', 't', 'yes', 'y', 'on', '1')
    FALSY = ('false', 'f', 'no', 'n', 'off', '0')

    def __init__(self, *args, **kwargs):
        self._spec_url = kwargs.pop('spec_url', None)
        self._resource_module = kwargs.pop('resource_module', None)
        self.validate_responses = kwargs.pop('validate_responses', True)
        self.serve_docs = kwargs.pop('serve_docs', '/_docs')
        with closing(urlopen(self._spec_url)) as fp:
            self.spec = yaml.load(fp)
            self._spec = JsonRef.replace_refs(self.spec)
        self._process_spec(self._spec)
        super(SwaggerApi, self).__init__(*args, **kwargs)
        if self.serve_docs is not None:
            self.add_resource(
                docs.Spec,
                '{}/swagger.yaml'.format(self.serve_docs))

    def init_app(self, app):
        super(SwaggerApi, self).init_app(app)

    def add_resource(self, resource, *urls, **kwargs):
        resource_class_args = tuple(kwargs.pop('resource_class_args', ()))
        resource_class_args = (self,) + resource_class_args
        kwargs['resource_class_args'] = resource_class_args
        return super(SwaggerApi, self).add_resource(resource, *urls, **kwargs)

    def _process_spec(self, spec):
        # Catalog the resources handling each path
        self._resource_paths = {}
        if self._resource_module:
            prefix = self._resource_module + '.'
        for path, pspec in spec['paths'].items():
            res = pspec.get('x-resource')
            if res:
                self._resource_paths[prefix + res] = pspec

    def _validate_response(self, resource, response):
        if not self.validate_responses:
            return
        method = request.method.lower()
        resp_spec = self._get_response(resource, method, response.status_code)
        if resp_spec is None:
            raise SwaggerError('Unknown response code {}'.format(
                response.status_code))
        schema_spec = resp_spec.get('schema', None)
        if schema_spec is None:
            return
        schema = DefaultValidatingDraft4Validator(schema_spec)
        json_data = json.loads(response.data)
        schema.validate(json_data)

    def _validate_parameters(self, resource, args, kwargs):
        method = request.method.lower()
        params_spec = self._get_params(resource, method)

        params = {}

        # Check the body param
        body_param_spec = [p for p in params_spec if p['in'] == 'body']
        if body_param_spec:
            data = request.json
            if data is None:
                data = request.form
            params['body'] = self._check_body_param(
                body_param_spec[0], data)

        # Check the primitive params
        params.update(self._check_primitive_params(
            params_spec, 'path', kwargs))
        params.update(self._check_primitive_params(
            params_spec, 'query', request.args))
        params.update(self._check_primitive_params(
            params_spec, 'header', request.headers))
        params.update(self._check_primitive_params(
            params_spec, 'form', request.form))
        return params

    def _get_params(self, resource, method):
        pspec = self._resource_paths.get(resource._resource_name(), {})
        ospec = pspec.get(method, {})
        params = pspec.get('parameters', []) + ospec.get('parameters', [])
        return params

    def _get_response(self, resource, method, status_code):
        pspec = self._resource_paths.get(resource._resource_name(), {})
        ospec = pspec.get(method, {})
        responses = ospec.get('responses', {})
        return responses.get(str(status_code), None)

    def _check_primitive_params(self, params_spec, ptype, data):
        """Validate and convert parameters of a certain primitive type.

        This will verify that *data* correctly validates, and will return
        the validated and converted data.

        Valid ptypes:
         - path
         - query
         - header
         - form
        """
        schema_spec = dict(
            type='object',
            properties={},
            required=[])

        res = dict(data)

        for param in params_spec:
            if param['in'] != ptype:
                continue
            p_name = param['name']
            p_type = param['type']
            p_format = param.get('format', None)
            if param['required']:
                schema_spec['required'].append(p_name)
            schema_spec['properties'][p_name] = p_sch = dict(type=p_type)
            if p_format:
                p_sch['format'] = p_format

            try:
                value = data[p_name]
            except KeyError:
                continue

            # Attempt primitive type conversion
            if p_type == 'integer':
                try:
                    res[p_name] = int(value)
                except:
                    pass
            elif p_type == 'number':
                try:
                    res[p_name] = float(value)
                except:
                    pass
            elif p_type == 'boolean':
                if value.lower() in self.TRUTHY:
                    res[p_name] = True
                elif value.lower() in self.FALSY:
                    res[p_name] = False

        schema = DefaultValidatingDraft4Validator(schema_spec)
        schema.validate(res)
        return res

    def _check_body_param(self, param_spec, data):
        schema = DefaultValidatingDraft4Validator(param_spec['type'])
        schema.validate(data)
        return data