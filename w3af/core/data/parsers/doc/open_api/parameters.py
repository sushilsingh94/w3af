# -*- coding: UTF-8 -*-
"""
requests.py

Copyright 2017 Andres Riancho

This file is part of w3af, http://w3af.org/ .

w3af is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation version 2 of the License.

w3af is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with w3af; if not, write to the Free Software
Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

"""
import copy
import random
import datetime

from w3af.core.data.fuzzer.form_filler import (smart_fill,
                                               smart_fill_file)


class OpenAPIParamResolutionException(Exception):
    pass


class ParameterHandler(object):

    DEFAULT_VALUES_BY_TYPE = {'int64': 42,
                              'int32': 42,
                              'integer': 42,
                              'float': 4.2,
                              'double': 4.2,
                              'date': datetime.date(2017, 06, 30),
                              'date-time': datetime.datetime(2017, 06, 30, 23, 59, 45),
                              'boolean': True}

    def __init__(self, spec, operation):
        """
        :param spec: The parsed specification. We need this to get the param value
                     in special cases where it's type is defined to a Model.
        :param operation: The REST API operation, eg. addStoreItem
        """
        self.spec = spec
        self.operation = operation

    def set_operation_params(self, optional=False):
        """
        This is the main entry point. We return a set with the required and
        optional parameters for the provided operation / specification.

        :param optional: Should we set the values for the optional parameters?
        """
        self._fix_common_spec_issues()

        operation = copy.deepcopy(self.operation)

        for parameter_name, parameter in operation.params.iteritems():
            # We make sure that all parameters have a fill attribute
            parameter.fill = None

            if not parameter.required and not optional:
                continue

            self._set_param_value(parameter)

        return operation

    def operation_has_optional_params(self):
        """
        :return: True if the operation has optional parameters
        """
        for parameter_name, parameter in self.operation.params.iteritems():
            if not parameter.required:
                return True

        return False

    def _fix_common_spec_issues(self):
        """
        Sometimes the openapi specification is manually written, modified by
        a developer, or automatically generated by a tool that will generate
        invalid OpenAPI documents.

        This method will try to fix some of the issues we've see in real life.

        :return: None, just modifies the self.operation and self.spec
        """
        self._fix_string_format()
        self._fix_string_with_invalid_format()
        self._fix_bad_default_for_number_type()

    def _fix_string_format(self):
        """
        Bravado core doesn't support using "string" as the format:

            {
                "default": "name,user_id",
                "type": "string",
                "name": "sort",
                "in": "query",
                "format": "string",         <--------- THIS
                "required": false,
                "description": "Sort order"
            }

        So this method will iterate through all the parameters in this
        operation and remove that.

        :return: None
        """
        for parameter_name, parameter in self.operation.params.iteritems():

            param_format = parameter.param_spec.get('format', None)
            param_type = parameter.param_spec.get('type', None)

            if param_format == 'string' and param_type == 'string':
                del parameter.param_spec['format']

    def _fix_string_with_invalid_format(self):
        """
        The specification [0] only allows some values for format when string
        type is used. Developers sometimes make this mistake:

            {
                "default": "",
                "type": "string",           <--------- THIS
                "name": "fields[Users]",
                "in": "query",
                "format": "int64",          <--------- THIS
                "required": false,
                "description": "Fields to be selected (csv)"
            }

        So this method will iterate through all the parameters in this
        operation and remove the invalid format.

        [0] https://swagger.io/specification/#dataTypes

        :return: None
        """
        invalid_formats = ['int32', 'int64', 'float', 'double', '']

        for parameter_name, parameter in self.operation.params.iteritems():

            param_format = parameter.param_spec.get('format', None)
            param_type = parameter.param_spec.get('type', None)

            if param_format in invalid_formats and param_type == 'string':
                del parameter.param_spec['format']

    def _fix_bad_default_for_number_type(self):
        """
        Sometimes developers set the default value to something that is not
        valid for the type / format they specify.

            {
                "default": "",              <--------- THIS
                "type": "string",
                "name": "fields[Users]",
                "in": "query",
                "format": "int64",          <--------- THIS
                "required": false,
                "description": "Fields to be selected (csv)"
            }

        >>> long('')
        ValueError: invalid literal for long() with base 10: ''

        Just set a default value of zero if an empty string is specified.

        :return: None
        """
        fix_formats = ['double', 'float', 'int32', 'int64']

        for parameter_name, parameter in self.operation.params.iteritems():

            param_format = parameter.param_spec.get('format', None)
            param_default = parameter.param_spec.get('default', None)

            if param_format not in fix_formats:
                continue

            if not isinstance(param_default, basestring):
                continue

            if param_default.isdigit():
                continue

            parameter.param_spec['default'] = 0

    def _set_param_value(self, parameter):
        """
        If the parameter has a default value, then we use that. If there is
        no value, we try to fill it with something that makes sense based on
        the parameter type and name.

        The value is set to the parameter.fill attribute

        :param parameter: The parameter for which we need to set a value
        :return: True if we were able to set the parameter value
        """
        #
        #   Easiest case, the parameter already has a default value
        #
        if parameter.default is not None:
            parameter.fill = parameter.default
            return True

        param_spec = parameter.param_spec

        value = self._get_param_value(param_spec)
        if value is not None:
            parameter.fill = value
            return True

        return False

    def _get_param_value(self, param_spec):
        """
        Receives a parameter specification and returns a valid value

        :param param_spec: The parameter specification
        :return: A valid value, string, int, dict, etc.
        """
        if 'schema' in param_spec:
            param_spec = param_spec['schema']

        value = self._get_param_value_for_primitive(param_spec)
        if value is not None:
            return value

        value = self._get_param_value_for_model(param_spec)
        if value is not None:
            return value

        # A default
        return 42

    def _get_param_value_for_type_and_spec(self, parameter_type, parameter_spec):
        """
        :param parameter_type: The type of parameter (string, int32, array, etc.)
        :param parameter_spec: The parameter spec
        :return: The parameter value
        """
        parameter_name = parameter_spec.get('name', None)

        # This handles the case where the value is an enum and can only be selected
        # from a predefined option list
        if 'enum' in parameter_spec:
            if parameter_spec['enum']:
                return parameter_spec['enum'][0]

        if parameter_type in ('integer', 'float', 'double', 'int32', 'int64'):
            _max = None
            _min = None

            if 'maximum' in parameter_spec:
                _max = parameter_spec['maximum']

            if 'minimum' in parameter_spec:
                _min = parameter_spec['minimum']

            # Only do something if max or min are set
            if _max is not None or _min is not None:
                _max = _max if _max is not None else 56
                _min = _min if _min is not None else 0

                # We always want to generate the same number for the same range
                r = random.Random()
                r.seed(1)

                return r.randint(_min, _max)

        default_value = self.DEFAULT_VALUES_BY_TYPE.get(parameter_type, None)
        if default_value is not None:
            return default_value

        if parameter_type == 'string':
            parameter_name = 'unknown' if parameter_name is None else parameter_name
            return smart_fill(parameter_name)

        if parameter_type == 'file':
            parameter_name = 'unknown' if parameter_name is None else parameter_name
            return smart_fill_file(parameter_name, 'cat.png')

    def _get_parameter_type(self, param_spec):
        """
        The parameter has a strong type:

            https://github.com/OAI/OpenAPI-Specification/blob/master/versions/2.0.md

        Fetch it and return.
        """
        try:
            parameter_type = param_spec['format']
        except KeyError:
            try:
                parameter_type = param_spec['type']
            except KeyError:
                # This is not a primitive type, most likely a model
                return None

        return parameter_type

    def _get_param_value_for_primitive(self, param_spec):
        """
        Handle the cases where the parameter is a primitive: int, string, float, etc.

        :param param_spec: The parameter spec which (might or might not) be of a primitive type
        :return: The parameter we just modified
        """
        parameter_type = self._get_parameter_type(param_spec)
        if parameter_type is None:
            return None

        value = self._get_param_value_for_type_and_spec(parameter_type,
                                                        param_spec)
        if value is not None:
            return value

        #
        # Arrays are difficult to handle since they can contain complex data
        #
        value = self._get_param_value_for_array(param_spec)

        if value is not None:
            return value

        # We should never reach here! The parameter.fill value was never
        # modified!
        return None

    def _get_param_value_for_array(self, param_spec):
        """
        :param param_spec: The parameter spec
        :return: A python list (json array) containing values
        """
        if param_spec.get('type', None) != 'array':
            return None

        if param_spec.get('items', None) is None:
            # Potentially invalid array specification, we just return
            # an empty array
            return []

        # Do we have a default value which can be used?
        if 'default' in param_spec['items']:
            return [param_spec['items']['default']]

        #
        # The array definition is a little bit more complex than just
        # returning [some-primitive-type]. For example it might
        # look like this:
        #
        #     u'photoUrls': {u'items': {u'type': u'string'},
        #                    u'type': u'array',
        #
        # Where this is completely valid: ['http://abc/']
        #
        # Or like this:
        #
        #     u'ids': {u'items': {u'type': u'int32'},
        #              u'type': u'array',
        #
        # Where we need to fill with integers: [1, 3, 4]
        #
        # Or even worse... there is a model in the array:
        #
        #     u'tags': {u'items': {u'$ref': u'#/definitions/Tag',
        #                           'x-scope': [u'http://moth/swagger.json',
        #                                      u'http://moth/swagger.json#/definitions/Pet']},
        #               u'type': u'array',
        #
        # And we need to fill the array with one or more tags
        #
        item_param_spec = param_spec['items']

        value = self._get_param_value(item_param_spec)
        if value is not None:
            return [value]

        return []

    def _get_param_value_for_model(self, param_spec):
        """
        Each model attribute can be of a primitive type or another model.

        We need to dereference the model until we have primitives for each field
        (as seen in http://bigstickcarpet.com/swagger-parser/www/index.html#)
        and then fill the value for each primitive.

        :param param_spec: The parameter specification instance
        :return: The parameter with a modified default attribute
        """
        parameter_definition = self._get_object_definition(param_spec)
        created_object = self._create_object(parameter_definition)

        if created_object is not None:
            return created_object

        raise NotImplementedError

    def _get_object_definition(self, param_spec):
        """
        This method calls `_get_object_definition_impl` until the object
        definition is completely dereferenced.

        In the most common cases we call `_get_object_definition_impl` only
        a couple of times to resolve things like `$ref` and `allOf`.

        :param param_spec: The parameter specification instance
        :return: The object definition which needs to be created
        """
        return self._get_object_definition_impl(param_spec)

    def _merge_all_parts(self, all_parts):
        """
        https://swagger.io/docs/specification/data-models/oneof-anyof-allof-not/

        When we receive an allOf we just merge all the properties from the
        different schemas / definitions / references / models into one big
        dict and return it.

        The output of this method looks like:

                {u'title': u'Pet',
                 u'x-model': u'Pet',
                 u'type': u'object',
                 u'properties': {u'age': {u'type': u'integer', u'format': u'int32'}},
                 u'required': [u'name']}

        :param all_parts: A list containing the `allOf`
        :return: The definition as shown above
        """
        merged = {'required': [],
                  'properties': {},
                  'type': 'object'}

        for part in all_parts:
            object_definition = self._get_object_definition_impl(part)

            if 'required' in object_definition:
                for required in object_definition['required']:
                    merged['required'].append(required)

            if 'properties' in object_definition:
                for property_name, property_def in object_definition['properties'].iteritems():
                    merged['properties'][property_name] = property_def

        return merged

    def _get_object_definition_impl(self, param_spec):
        """
        :param param_spec: The parameter specification instance
        :return: The object definition which needs to be created
        """
        if '$ref' in param_spec:
            ref = {'$ref': param_spec['$ref']}
            param_spec = self.spec.deref(ref)

        if 'allOf' in param_spec:
            all_parts = param_spec['allOf']
            param_spec = self._merge_all_parts(all_parts)

        if 'schema' in param_spec:
            if '$ref' in param_spec['schema']:
                ref = {'$ref': param_spec['schema']['$ref']}
                param_spec = self.spec.deref(ref)
            else:
                # The definition is not a reference, the param_spec['schema'] looks like:
                #
                # {u'title': u'Pet',
                #  u'x-model': u'Pet',
                #  u'type': u'object',
                #  u'properties': {u'age': {u'type': u'integer', u'format': u'int32'}},
                #  u'required': [u'name']}
                param_spec = param_spec['schema']

        if 'type' in param_spec:
            if param_spec['type'] == 'object':
                # In this case the param_spec holds these values:
                #
                # {u'x-model': u'Pet Owner',
                #  u'name': u'owner',
                #  u'title': u'Pet Owner',
                #  u'required': [u'name'],
                #  u'type': u'object',
                #  u'properties': '...'}
                pass

        return param_spec

    def _create_object(self, param_spec):
        """
        Takes the output of a swagger_spec.deref() cal and creates an object.

        The output of swagger_spec.deref looks like:

        {u'required': [u'name'],
         u'type': u'object',
         u'properties': {u'tag': {u'type': u'string'},
                         u'name': {u'type': u'string'}},
         u'x-model': u'http:....www.w3af.com..swagger.json|..definitions..Pet'}

        :return: A dict containing all the fields specified in properties.
        """
        if param_spec.get('type', None) != 'object':
            return {}

        created_object = {}

        for property_name, property_data in param_spec['properties'].iteritems():

            # This helps us choose a better value for filling the parameter
            if 'name' not in property_data:
                property_data['name'] = property_name

            value = self._get_param_value(property_data)
            created_object[property_name] = value

        return created_object
