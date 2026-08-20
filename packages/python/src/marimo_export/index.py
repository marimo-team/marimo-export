from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, TypeAlias, cast

from marimo_export._format import (
    MAX_CONTROL_ID_BYTES as _MAX_CONTROL_ID_BYTES,
)
from marimo_export._format import (
    MAX_NAME_BYTES as _MAX_NAME_BYTES,
)
from marimo_export._format import (
    bounded_printable as _bounded_printable,
)
from marimo_export._format import (
    digest as _digest,
)
from marimo_export._format import (
    exact_fields as _exact_fields,
)
from marimo_export._format import (
    export_name as _export_name,
)
from marimo_export._format import (
    name_array as _name_array,
)
from marimo_export._format import (
    object_value as _object,
)
from marimo_export._format import (
    opaque_name as _opaque_name,
)
from marimo_export._format import (
    ordered_names as _ordered_names,
)
from marimo_export._json import (
    JsonObject,
    JsonValue,
    canonical_bytes,
    decode_json_object,
    json_object,
    json_string,
    portable_json_object,
)
from marimo_export._portable import validate_portable_basename
from marimo_export.descriptors import (
    ARROW_CODEC,
    BLOB_ASSET_CODEC,
    MARIMO_CELL_CODEC,
    MARIMO_OUTPUT_CODEC,
    NUMPY_CODEC,
    ArrowDescriptor,
    AssetRef,
    BlobAssetDescriptor,
    JsonDescriptor,
    MarimoCellDescriptor,
    MarimoOutputDescriptor,
    NumpyDescriptor,
    OutputCodec,
    OutputDescriptor,
    ScalarDescriptor,
    _descriptor,
    _descriptor_asset_facts,
)
from marimo_export.errors import NotebookExportError
from marimo_export.wire import state_fingerprint

EXPORT_SCHEMA = "marimo-export.export.v1"
_MAX_INDEX_BYTES = 16 * 1024 * 1024
_MAX_INDEX_VALUES = 2_000_000
_MAX_SAFE_INTEGER = 2**53 - 1
_MAX_CONTROL_PATH_STEPS = 256


@dataclass(frozen=True, slots=True)
class NotebookProvenance:
    filename: str | None
    document_sha256: str

    def __post_init__(self) -> None:
        if self.filename is not None:
            try:
                validate_portable_basename(self.filename, "notebook.filename")
            except (TypeError, ValueError) as error:
                raise ValueError("notebook.filename must be a portable basename or null") from error
        _digest(self.document_sha256, "notebook.document_sha256")

    def to_value(self) -> JsonObject:
        return {
            "filename": self.filename,
            "document_sha256": self.document_sha256,
        }


@dataclass(frozen=True, slots=True)
class ProducerProvenance:
    marimo: str
    marimo_export: str
    implementation_sha256: str

    def __post_init__(self) -> None:
        _bounded_printable(self.marimo, "producer.marimo", _MAX_NAME_BYTES)
        _bounded_printable(
            self.marimo_export,
            "producer.marimo_export",
            _MAX_NAME_BYTES,
        )
        _digest(self.implementation_sha256, "producer.implementation_sha256")

    def to_value(self) -> JsonObject:
        return {
            "marimo": self.marimo,
            "marimo_export": self.marimo_export,
            "implementation_sha256": self.implementation_sha256,
        }


@dataclass(frozen=True, slots=True)
class ControlIndexStep:
    value: int
    kind: Literal["index"] = field(default="index", init=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.value, int)
            or isinstance(self.value, bool)
            or self.value < 0
            or self.value > _MAX_SAFE_INTEGER
        ):
            raise ValueError("control index path step must have a nonnegative safe integer")

    def to_value(self) -> JsonObject:
        return {"kind": self.kind, "value": self.value}


@dataclass(frozen=True, slots=True)
class ControlKeyStep:
    value: str
    kind: Literal["key"] = field(default="key", init=False)

    def __post_init__(self) -> None:
        key = json_string(self.value, "control key path step")
        if len(key.encode("utf-8")) > _MAX_CONTROL_ID_BYTES:
            raise ValueError(
                f"control key path step must contain at most {_MAX_CONTROL_ID_BYTES} UTF-8 bytes"
            )

    def to_value(self) -> JsonObject:
        return {"kind": self.kind, "value": self.value}


@dataclass(frozen=True, slots=True)
class ControlElementStep:
    kind: Literal["element"] = field(default="element", init=False)

    def to_value(self) -> JsonObject:
        return {"kind": self.kind}


ControlPathStep: TypeAlias = ControlIndexStep | ControlKeyStep | ControlElementStep


@dataclass(frozen=True, slots=True)
class ControlBinding:
    input: str
    path: tuple[ControlPathStep, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "input", _opaque_name(self.input, "control binding input"))
        if not isinstance(self.path, tuple) or len(self.path) > _MAX_CONTROL_PATH_STEPS:
            raise ValueError(
                f"control binding path must be a tuple of at most {_MAX_CONTROL_PATH_STEPS} steps"
            )
        if any(
            not isinstance(step, (ControlIndexStep, ControlKeyStep, ControlElementStep))
            for step in self.path
        ):
            raise TypeError("control binding path contains an invalid step")

    def to_value(self) -> JsonObject:
        return {
            "input": self.input,
            "path": [step.to_value() for step in self.path],
        }


@dataclass(frozen=True, slots=True, init=False)
class StateEntry:
    outputs: Mapping[str, OutputDescriptor]
    _inputs_bytes: bytes = field(repr=False)

    def __init__(
        self,
        *,
        inputs: Mapping[str, JsonValue],
        outputs: Mapping[str, OutputDescriptor],
    ) -> None:
        input_value = portable_json_object(inputs, "state.inputs")
        if not isinstance(outputs, Mapping) or not outputs:
            raise ValueError("state.outputs must contain at least one output")
        parsed_outputs: dict[str, OutputDescriptor] = {}
        for name, descriptor in outputs.items():
            export_name = _export_name(name, "state.outputs key")
            if not isinstance(
                descriptor,
                (
                    ScalarDescriptor,
                    JsonDescriptor,
                    MarimoOutputDescriptor,
                    MarimoCellDescriptor,
                    NumpyDescriptor,
                    ArrowDescriptor,
                    BlobAssetDescriptor,
                ),
            ):
                raise TypeError(f"state.outputs[{export_name!r}] has an invalid descriptor")
            parsed_outputs[export_name] = descriptor
        object.__setattr__(self, "_inputs_bytes", canonical_bytes(input_value))
        object.__setattr__(self, "outputs", MappingProxyType(parsed_outputs))

    @property
    def inputs(self) -> JsonObject:
        return decode_json_object(self._inputs_bytes, "state.inputs")

    def to_value(self) -> JsonObject:
        return {
            "inputs": self.inputs,
            "outputs": {name: descriptor.to_value() for name, descriptor in self.outputs.items()},
        }


@dataclass(frozen=True, slots=True)
class ExportIndex:
    spec_sha256: str
    default_state: str
    notebook: NotebookProvenance
    producer: ProducerProvenance
    inputs: tuple[str, ...]
    control_bindings: Mapping[str, ControlBinding]
    outputs: tuple[str, ...]
    aliases: Mapping[str, str]
    states: Mapping[str, StateEntry]

    def __post_init__(self) -> None:
        spec_sha256 = _digest(self.spec_sha256, "export.spec_sha256")
        default_state = _digest(self.default_state, "export.default_state")
        if not isinstance(self.notebook, NotebookProvenance):
            raise TypeError("export.notebook must be NotebookProvenance")
        if not isinstance(self.producer, ProducerProvenance):
            raise TypeError("export.producer must be ProducerProvenance")
        if not isinstance(self.inputs, tuple):
            raise TypeError("export.inputs must be a tuple")
        parsed_inputs = tuple(_opaque_name(value, "export.inputs item") for value in self.inputs)
        if len(parsed_inputs) != len(set(parsed_inputs)):
            raise ValueError("export.inputs must contain unique names")
        if not isinstance(self.control_bindings, Mapping):
            raise TypeError("export.control_bindings must be a mapping")
        parsed_control_bindings: dict[str, ControlBinding] = {}
        input_set = set(parsed_inputs)
        for object_id, binding in self.control_bindings.items():
            control_id = _bounded_printable(
                object_id,
                "export.control_bindings key",
                _MAX_CONTROL_ID_BYTES,
            )
            if not isinstance(binding, ControlBinding):
                raise TypeError(f"export.control_bindings[{control_id!r}] must be ControlBinding")
            if binding.input not in input_set:
                raise ValueError(
                    f"export.control_bindings[{control_id!r}].input must name a declared input"
                )
            parsed_control_bindings[control_id] = binding
        parsed_outputs = _ordered_names(
            self.outputs,
            "export.outputs",
            identifier=False,
            nonempty=True,
        )
        if not isinstance(self.states, Mapping) or not self.states:
            raise ValueError("export.states must contain at least one state")
        parsed_states: dict[str, StateEntry] = {}
        output_set = set(parsed_outputs)
        representation: dict[str, tuple[str, str]] = {}
        for key, state in self.states.items():
            fingerprint = _digest(key, "export.states key")
            if not isinstance(state, StateEntry):
                raise TypeError(f"export.states[{fingerprint!r}] must be StateEntry")
            if set(state.inputs) != input_set:
                raise ValueError(f"export.states[{fingerprint!r}].inputs must equal export.inputs")
            if set(state.outputs) != output_set:
                raise ValueError(
                    f"export.states[{fingerprint!r}].outputs must equal export.outputs"
                )
            if state_fingerprint(state.inputs) != fingerprint:
                raise ValueError(f"export.states key {fingerprint!r} does not match state inputs")
            for output_name, descriptor in state.outputs.items():
                current = (descriptor.codec, descriptor.media_type)
                previous = representation.setdefault(output_name, current)
                if previous != current:
                    raise ValueError(
                        f"output {output_name!r} changes codec or media type across states"
                    )
            parsed_states[fingerprint] = state
        if default_state not in parsed_states:
            raise ValueError("export.default_state must reference a declared state fingerprint")
        if not isinstance(self.aliases, Mapping):
            raise TypeError("export.aliases must be a mapping")
        parsed_aliases: dict[str, str] = {}
        for name, target in self.aliases.items():
            alias = _export_name(name, "export.aliases key")
            fingerprint = _digest(target, f"export.aliases[{alias!r}]")
            if fingerprint not in parsed_states:
                raise ValueError(
                    f"export.aliases[{alias!r}] references an unknown state fingerprint"
                )
            parsed_aliases[alias] = fingerprint
        object.__setattr__(self, "spec_sha256", spec_sha256)
        object.__setattr__(self, "default_state", default_state)
        object.__setattr__(self, "inputs", parsed_inputs)
        object.__setattr__(
            self,
            "control_bindings",
            MappingProxyType(parsed_control_bindings),
        )
        object.__setattr__(self, "outputs", parsed_outputs)
        object.__setattr__(self, "aliases", MappingProxyType(parsed_aliases))
        object.__setattr__(self, "states", MappingProxyType(parsed_states))
        self.assets()

    def to_value(self) -> JsonObject:
        return {
            "schema": EXPORT_SCHEMA,
            "spec_sha256": self.spec_sha256,
            "default_state": self.default_state,
            "notebook": self.notebook.to_value(),
            "producer": self.producer.to_value(),
            "inputs": list(self.inputs),
            "control_bindings": {
                object_id: binding.to_value()
                for object_id, binding in self.control_bindings.items()
            },
            "outputs": list(self.outputs),
            "aliases": dict(self.aliases),
            "states": {fingerprint: state.to_value() for fingerprint, state in self.states.items()},
        }

    def to_bytes(self) -> bytes:
        data = canonical_bytes(self.to_value())
        if len(data) > _MAX_INDEX_BYTES:
            raise NotebookExportError(
                f"canonical index exceeds {_MAX_INDEX_BYTES} bytes",
                code="export_invalid",
            )
        return data

    @classmethod
    def from_value(cls, value: object) -> ExportIndex:
        try:
            root = json_object(value, "export")
            _exact_fields(
                root,
                {
                    "schema",
                    "spec_sha256",
                    "default_state",
                    "notebook",
                    "producer",
                    "inputs",
                    "control_bindings",
                    "outputs",
                    "aliases",
                    "states",
                },
                "export",
            )
            if root["schema"] != EXPORT_SCHEMA:
                raise ValueError(f"export.schema must be {EXPORT_SCHEMA!r}")
            spec_sha256 = _digest(root["spec_sha256"], "export.spec_sha256")
            default_state = _digest(root["default_state"], "export.default_state")
            notebook = _notebook(root["notebook"])
            producer = _producer(root["producer"])
            inputs = _name_array(root["inputs"], "export.inputs")
            control_bindings_value = _object(
                root["control_bindings"],
                "export.control_bindings",
            )
            control_bindings = {
                object_id: _control_binding(binding, object_id)
                for object_id, binding in control_bindings_value.items()
            }
            outputs = _name_array(root["outputs"], "export.outputs")
            aliases_value = _object(root["aliases"], "export.aliases")
            aliases = {
                _export_name(name, "export.aliases key"): _digest(
                    target,
                    f"export.aliases[{name!r}]",
                )
                for name, target in aliases_value.items()
            }
            states_value = _object(root["states"], "export.states")
            states = {
                _digest(fingerprint, "export.states key"): _state(item, fingerprint)
                for fingerprint, item in states_value.items()
            }
            return cls(
                spec_sha256=spec_sha256,
                default_state=default_state,
                notebook=notebook,
                producer=producer,
                inputs=inputs,
                control_bindings=control_bindings,
                outputs=outputs,
                aliases=aliases,
                states=states,
            )
        except NotebookExportError:
            raise
        except (TypeError, ValueError) as error:
            raise NotebookExportError(
                f"invalid export index: {error}",
                code="export_invalid",
            ) from error

    @classmethod
    def from_bytes(cls, data: bytes) -> ExportIndex:
        if not isinstance(data, bytes):
            raise TypeError("export index must be bytes")
        if len(data) > _MAX_INDEX_BYTES:
            raise NotebookExportError(
                f"export index exceeds {_MAX_INDEX_BYTES} bytes",
                code="export_invalid",
            )
        try:
            root = decode_json_object(
                data,
                "export",
                max_values=_MAX_INDEX_VALUES,
            )
        except (TypeError, ValueError) as error:
            raise NotebookExportError(
                f"invalid export index: {error}",
                code="export_invalid",
            ) from error
        index = cls.from_value(root)
        if index.to_bytes() != data:
            raise NotebookExportError(
                "export index is not canonical JSON",
                code="export_noncanonical",
            )
        return index

    def assets(self) -> tuple[tuple[OutputCodec, AssetRef], ...]:
        assets: dict[tuple[str, str], tuple[AssetRef, tuple[object, ...]]] = {}
        total = 0
        for _, _, descriptor in self.descriptor_entries():
            if isinstance(descriptor, (ScalarDescriptor, JsonDescriptor)):
                continue
            identity = (descriptor.codec, descriptor.asset.sha256)
            facts = _descriptor_asset_facts(descriptor)
            previous = assets.get(identity)
            if previous is None:
                assets[identity] = (descriptor.asset, facts)
                total += descriptor.asset.size
                if total > _MAX_SAFE_INTEGER:
                    raise ValueError("aggregate unique asset size exceeds the safe integer range")
            elif previous != (descriptor.asset, facts):
                raise ValueError(f"asset identity {identity!r} has conflicting descriptor facts")
        order = {
            MARIMO_OUTPUT_CODEC: 0,
            MARIMO_CELL_CODEC: 1,
            NUMPY_CODEC: 2,
            ARROW_CODEC: 3,
            BLOB_ASSET_CODEC: 4,
        }
        return tuple(
            (cast(OutputCodec, codec), value[0])
            for (codec, _), value in sorted(
                assets.items(),
                key=lambda item: (order[item[0][0]], item[0][1]),
            )
        )

    def descriptor_entries(self) -> Iterator[tuple[str, str, OutputDescriptor]]:
        for state_name, state in self.states.items():
            for output_name, descriptor in state.outputs.items():
                yield state_name, output_name, descriptor


def _control_binding(value: object, object_id: str) -> ControlBinding:
    path = f"export.control_bindings[{object_id!r}]"
    item = _object(value, path)
    _exact_fields(item, {"input", "path"}, path)
    steps = item["path"]
    if not isinstance(steps, list) or len(steps) > _MAX_CONTROL_PATH_STEPS:
        raise ValueError(f"{path}.path must be an array of at most {_MAX_CONTROL_PATH_STEPS} steps")
    return ControlBinding(
        input=cast(str, item["input"]),
        path=tuple(
            _control_path_step(step, f"{path}.path[{index}]") for index, step in enumerate(steps)
        ),
    )


def _control_path_step(value: object, path: str) -> ControlPathStep:
    item = _object(value, path)
    kind = item.get("kind")
    if kind == "element":
        _exact_fields(item, {"kind"}, path)
        return ControlElementStep()
    if kind == "index":
        _exact_fields(item, {"kind", "value"}, path)
        return ControlIndexStep(value=cast(int, item["value"]))
    if kind == "key":
        _exact_fields(item, {"kind", "value"}, path)
        return ControlKeyStep(value=cast(str, item["value"]))
    raise ValueError(f"{path}.kind must be index, key, or element")


def _notebook(value: object) -> NotebookProvenance:
    item = _object(value, "export.notebook")
    _exact_fields(item, {"filename", "document_sha256"}, "export.notebook")
    filename = item["filename"]
    if filename is not None and not isinstance(filename, str):
        raise TypeError("export.notebook.filename must be a string or null")
    return NotebookProvenance(
        filename=filename,
        document_sha256=cast(str, item["document_sha256"]),
    )


def _producer(value: object) -> ProducerProvenance:
    item = _object(value, "export.producer")
    _exact_fields(
        item,
        {"marimo", "marimo_export", "implementation_sha256"},
        "export.producer",
    )
    return ProducerProvenance(
        marimo=cast(str, item["marimo"]),
        marimo_export=cast(str, item["marimo_export"]),
        implementation_sha256=cast(str, item["implementation_sha256"]),
    )


def _state(value: object, fingerprint: str) -> StateEntry:
    path = f"export.states[{fingerprint!r}]"
    item = _object(value, path)
    _exact_fields(item, {"inputs", "outputs"}, path)
    output_values = _object(item["outputs"], f"{path}.outputs")
    return StateEntry(
        inputs=_object(item["inputs"], f"{path}.inputs"),
        outputs={
            _export_name(name, f"{path}.outputs key"): _descriptor(descriptor, name)
            for name, descriptor in output_values.items()
        },
    )


__all__ = [
    "EXPORT_SCHEMA",
    "ControlBinding",
    "ControlElementStep",
    "ControlIndexStep",
    "ControlKeyStep",
    "ControlPathStep",
    "ExportIndex",
    "NotebookProvenance",
    "ProducerProvenance",
    "StateEntry",
]
