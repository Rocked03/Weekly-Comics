from typing import Type, TypeVar, Any
from dataclasses import fields, is_dataclass

T = TypeVar('T')


def from_dict(data_class: Type[T], data: dict) -> T:
    if not is_dataclass(data_class):
        raise ValueError(f"{data_class} is not a dataclass")

    field_types = {f.name: f.type for f in fields(data_class)}
    init_args = {}

    for field_name, field_type in field_types.items():
        if field_name in data:
            value = data[field_name]
            if is_dataclass(field_type):
                init_args[field_name] = from_dict(field_type, value)
            elif hasattr(field_type, '__origin__') and field_type.__origin__ == list:
                inner_type = field_type.__args__[0]
                init_args[field_name] = [from_dict(inner_type, item) if is_dataclass(inner_type) else item for item in
                                         value]
            else:
                init_args[field_name] = value

    return data_class(**init_args)