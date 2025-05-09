# Licensed under a 3-clause BSD style license - see LICENSE.rst

import re


# return_result included for backward compatibility
def find_fits_keyword(schema, keyword, return_result=False):
    """
    Find references to a FITS keyword in a given schema.

    This is intended for interactive use, and not for use
    within library code.

    Parameters
    ----------
    schema : JSON schema fragment
        The schema in which to search.

    keyword : str
        A FITS keyword name

    Returns
    -------
    locations : list of str
        A list of paths to instances of the keyword in the schema
    """

    def find_fits_keyword(subschema, path, combiner, ctx, recurse):
        if len(path) and path[0] == "extra_fits":
            return True
        if subschema.get("fits_keyword") == keyword:
            results.append(".".join(path))

    results = []
    walk_schema(schema, find_fits_keyword, results)

    return results


class SearchSchemaResults(list):
    """A list of search results with nicely-formatted string representation."""

    def __repr__(self):
        import textwrap

        result = []
        for path, description in self:
            result.append(path)
            result.append(
                textwrap.fill(description, initial_indent="    ", subsequent_indent="    ")
            )
        return "\n".join(result)


def search_schema(schema, substring):
    """
    Search the metadata schema for a particular phrase.

    This is intended for interactive use, and not for use within
    library code.

    The searching is case insensitive.

    Parameters
    ----------
    schema : JSON schema fragment
        The schema in which to search.

    substring : str
        The substring to search for.

    Returns
    -------
    locations : list of tuples
        A list of tuples, each containing a path to the substring
        and a description of the schema item at that path.
    """
    substring = substring.lower()

    def find_substring(subschema, path, combiner, ctx, recurse):
        matches = False
        for param in ("title", "description"):
            if substring in schema.get(param, "").lower():
                matches = True
                break

        if substring in ".".join(path).lower():
            matches = True

        if matches:
            description = "\n\n".join(
                [schema.get("title", ""), schema.get("description", "")]
            ).strip()
            results.append((".".join(path), description))

    results = SearchSchemaResults()
    walk_schema(schema, find_substring, results)
    results.sort()
    return results


def walk_schema(schema, callback, ctx=None):
    """
    Walk a JSON schema tree in breadth-first order, calling a callback function at each entry.

    Parameters
    ----------
    schema : JSON schema
        The schema to walk

    callback : callable
        The callback receives the following arguments at each entry:

        - subschema: The subschema for the entry
        - path: A list of strings defining the path to the entry
        - combiner: The current combiner in effect, will be 'allOf',
          'anyOf', 'oneOf', 'not' or None
        - ctx: An arbitrary context object, usually a dictionary
        - recurse: A function to call to recurse deeper on a node.

        If the callback returns `True`, the subschema will not be
        further recursed.

    ctx : object, optional
        An arbitrary context object
    """

    def recurse(schema, path, combiner, ctx):
        if callback(schema, path, combiner, ctx, recurse):  # noqa: F821
            return

        for c in ["allOf", "not"]:
            for sub in schema.get(c, []):
                recurse(sub, path, c, ctx)  # noqa: F821

        for c in ["anyOf", "oneOf"]:
            for _, sub in enumerate(schema.get(c, [])):
                recurse(sub, path + [c], c, ctx)  # noqa: F821

        if schema.get("type") == "object":
            for key, val in schema.get("properties", {}).items():
                recurse(val, path + [key], combiner, ctx)  # noqa: F821

        if schema.get("type") == "array":
            items = schema.get("items", {})
            if isinstance(items, list):
                for item in items:
                    recurse(item, path + ["items"], combiner, ctx)  # noqa: F821
            elif len(items):
                recurse(items, path + ["items"], combiner, ctx)  # noqa: F821

    if ctx is None:
        ctx = {}
    recurse(schema, [], None, ctx)
    # testing memory usage and garbage collection revealed that recurse
    # was difficult to garbage collect (often resulting in models and associated
    # data ending up in generation 2 for the garbage collector).
    # calling del here seems to improve the situation (one test used 1/2 the memory
    # with this del)
    # see the following PR for more information
    # https://github.com/spacetelescope/stdatamodels/pull/109
    del recurse


def merge_property_trees(schema):
    """
    Recursively merges property trees that are governed by the "allOf" combiner.

    The main purpose of this function is to allow multiple subschemas to be
    combined into a single schema. All of the properties at each level of each
    subschema are merged together to form a single coherent tree.

    This allows datamodel schemas to be more modular, since various components
    can be represented in individual files and then referenced elsewhere. They
    are then combined by this function into a single schema data structure.

    Parameters
    ----------
    schema : JSON schema
        The schema to be merged

    Returns
    -------
    JSON schema
        The merged schema
    """
    # track the "combined" and "top" items separately
    # this allows the top level "id", "$schema", etc to overwrite
    # combined items (so that the schema["id"] doesn't change).
    combined_items = {}
    top_items = {}

    def add_entry(path, schema, combiner):
        if not top_items:
            top_items.update(schema)
        # TODO: Simplify?
        cursor = combined_items
        for i in range(len(path)):
            part = path[i]
            if part == combiner:
                cursor = cursor.setdefault(combiner, [])
                return
            elif isinstance(part, int):
                cursor = cursor.setdefault("items", [])
                while len(cursor) <= part:
                    cursor.append({})
                cursor = cursor[part]
            elif part == "items":
                cursor = cursor.setdefault("items", {})
            else:
                cursor = cursor.setdefault("properties", {})
                if i < len(path) - 1 and isinstance(path[i + 1], int):
                    cursor = cursor.setdefault(part, [])
                else:
                    cursor = cursor.setdefault(part, {})

        cursor.update(schema)

    def callback(schema, path, combiner, ctx, recurse):
        schema_type = schema.get("type")
        schema = dict(schema)  # shallow copy
        if schema_type == "object":
            if "properties" in schema:
                del schema["properties"]
        elif schema_type == "array":
            del schema["items"]
        if "allOf" in schema:
            del schema["allOf"]

        add_entry(path, schema, combiner)

    walk_schema(schema, callback)

    return combined_items | top_items


def build_docstring(klass, template="{fits_hdu} {title}"):
    """
    Build a docstring for the specified DataModel class from its schema.

    Parameters
    ----------
    klass : Python class
        A class instance of a datamodel
    template : str
        A string format template to be applied to each schema item

    Returns
    -------
    field_info : str
        Information about each schema item associated with a FITS hdu
    """
    from . import model_base

    def get_field_info(subschema, path, combiner, info, recurse):
        # Return all schema fields representing fits hdus
        if "fits_hdu" in subschema and "fits_keyword" not in subschema:
            attr = ".".join(path)
            info[attr] = subschema
        return "fits_hdu" in subschema or "fits_keyword" in subschema

    # Silly rabbit, only datamodels have schemas
    if not (klass == model_base.DataModel or issubclass(klass, model_base.DataModel)):
        raise ValueError("Class must be a subclass of DataModel: %s", klass.__name__)

    # Create a new model just to get its shape
    null_object = klass(init=None)
    shape = null_object.shape
    if shape is None:
        shaped_object = null_object
    else:
        # Instantiate an object with correctly dimensioned shape
        null_object.close()
        shape = tuple([1 for i in range(len(shape))])
        shaped_object = klass(init=shape)

    # Get schema fields which have an associated hdu
    info = {}
    walk_schema(shaped_object._schema, get_field_info, ctx=info)

    # Extract field names from template to set defaults
    # so format won't crash while using them when they aren't there
    default_schema = {}
    fields = re.findall(r"\{([^\\:}]*)[\:\}]", template)
    for field in fields:
        default_schema[field] = ""

    buffer = []
    for attr, subschema in info.items():
        schema = {}
        schema.update(default_schema)
        schema.update(subschema)
        schema["path"] = attr

        # Determine if attribute has a default value
        instance = shaped_object.instance
        for field in attr.split("."):
            try:
                instance = instance.get(field)
            except AttributeError:
                instance = None
            if instance is None:
                break
        schema["default"] = instance is not None

        # Extract table field names from datatype
        if isinstance(schema["datatype"], str):
            schema["array"] = True
        else:
            schema["records"] = True
            fields = []
            for field_info in schema["datatype"]:
                fields.append(field_info["name"])
            schema["fields"] = ", ".join(fields)
            schema["datatype"] = "table"

        # Convert boolean fields to their field names
        for field, value in schema.items():
            if isinstance(value, bool):
                schema[field] = field

        # Apply format to schema fields
        # Delete blank lines
        lines = template.format(**schema)
        for line in lines.split("\n"):
            if line and not line.isspace():
                buffer.append(line)

    field_info = "\n".join(buffer) + "\n"
    return field_info
