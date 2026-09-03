function isPrimitiveWithPrototype<Value, Prototype>(value: Value, prototype: Prototype): boolean {
  const boxed = Object(value);
  return boxed !== value && Object.getPrototypeOf(boxed) === prototype;
}

export function isStringValue<Value>(value: Value): value is Value & string {
  return isPrimitiveWithPrototype(value, String.prototype);
}

export function isNumberValue<Value>(value: Value): value is Value & number {
  return isPrimitiveWithPrototype(value, Number.prototype);
}

export function isBooleanValue<Value>(value: Value): value is Value & boolean {
  return isPrimitiveWithPrototype(value, Boolean.prototype);
}

export function isRecordValue<Value>(value: Value): value is Value & object {
  return (
    value !== null &&
    !Array.isArray(value) &&
    Object.prototype.toString.call(value) === "[object Object]"
  );
}

export function isPropertyOwner<Value>(value: Value): value is Value & object {
  return value !== null && Object(value) === value;
}

export function isCallableValue<Value>(
  value: Value,
): value is Value & ((...arguments_: never[]) => void) {
  try {
    Function.prototype.toString.call(value);
    return true;
  } catch {
    return false;
  }
}
