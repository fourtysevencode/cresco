const PROPERTY_ERROR = /TypeError: Cannot read properties of (undefined|null) \(reading '([^']+)'\)/;
const STACK_FRAME = /^\s*at (?:(?:.+?) \()?(.+\.[cm]?[jt]s):(\d+):(\d+)\)?\s*$/m;
const PYTHON_FRAME = /^\s*File "([^"]+\.py)", line (\d+), in .+$/gm;

export function diagnose(log) {
  if (typeof log !== 'string' || log.length > 100_000) {
    throw new Error('A log string under 100 KB is required');
  }
  log = log
    .replace(/Bearer\s+[^\s"']+/gi, 'Bearer [REDACTED]')
    .replace(/\b(api[_-]?key|token|password|secret)\s*[:=]\s*["']?[^\s"',}]+/gi, '$1=[REDACTED]');

  const property = log.match(PROPERTY_ERROR);
  const frame = log.match(STACK_FRAME);
  const pythonFrames = [...log.matchAll(PYTHON_FRAME)];
  const pythonFrame = pythonFrames.at(-1);
  const location = frame ? {
    file: frame[1].replaceAll('\\', '/'),
    line: Number(frame[2]),
    column: Number(frame[3])
  } : pythonFrame ? {
    file: pythonFrame[1].replaceAll('\\', '/'),
    line: Number(pythonFrame[2]),
    column: null
  } : null;

  if (property) {
    return {
      kind: 'null-property',
      title: `Missing value before .${property[2]}`,
      rootCause: `The API accessed '${property[2]}' on ${property[1]}. The first application stack frame identifies where the missing value was used.`,
      evidence: property[0],
      location,
      confidence: location ? 'high' : 'medium',
      nextStep: 'Check the input or data lookup that supplies this value, then handle the missing case explicitly.'
    };
  }

  const pythonMissing = log.match(/(?:KeyError: ['"]([^'"]+)['"]|AttributeError: 'NoneType' object has no attribute ['"]([^'"]+)['"])/);
  if (pythonMissing) {
    const key = pythonMissing[1] ?? pythonMissing[2];
    return {
      kind: 'python-missing-value',
      title: `Missing Python value: ${key}`,
      rootCause: pythonMissing[1]
        ? `The code looked up '${key}' in a dictionary where that key was absent.`
        : `The code accessed '${key}' on None.`,
      evidence: pythonMissing[0],
      location,
      confidence: location ? 'high' : 'medium',
      nextStep: 'Handle the missing field explicitly and return a stable API response.'
    };
  }

  if (/ECONNREFUSED/i.test(log)) {
    return {
      kind: 'dependency-unavailable',
      title: 'Dependency connection refused',
      rootCause: 'The API tried to connect to a service that did not accept the connection.',
      evidence: log.match(/[^\n]*ECONNREFUSED[^\n]*/i)?.[0]?.trim() ?? 'ECONNREFUSED',
      location,
      confidence: 'medium',
      nextStep: 'Check the service URL, port, startup order, and availability. A code patch alone may not fix this.'
    };
  }

  if (/SyntaxError: Unexpected token/i.test(log)) {
    return {
      kind: 'invalid-json',
      title: 'Invalid JSON input',
      rootCause: 'The API attempted to parse content that is not valid JSON.',
      evidence: log.match(/SyntaxError: Unexpected token[^\n]*/i)?.[0] ?? 'SyntaxError',
      location,
      confidence: 'medium',
      nextStep: 'Validate the request content type and return a clear 400 response for malformed JSON.'
    };
  }

  return {
    kind: 'unknown',
    title: 'Failure needs investigation',
    rootCause: 'The current rules cannot establish a root cause from this log.',
    evidence: log.split('\n').find(Boolean)?.slice(0, 300) ?? '',
    location,
    confidence: 'low',
    nextStep: 'Inspect the complete stack trace and surrounding request context before proposing a patch.'
  };
}

// A deliberately narrow, reviewable repair for the bundled live demo.
// Other errors are diagnosed but never changed by a guessed text replacement.
export function proposePatch(diagnosis, source, lineNumber) {
  if (!Number.isInteger(lineNumber)) return null;
  const lines = source.split('\n');
  const before = lines[lineNumber - 1];
  if (!before) return null;
  let after;
  if (diagnosis.kind === 'null-property' && before.includes('student.progress.completed')) {
    after = before.replace('student.progress.completed', '(student.progress?.completed ?? 0)');
  } else if (diagnosis.kind === 'python-missing-value' && before.includes('student["progress"]["completed"]')) {
    after = before.replace('student["progress"]["completed"]', '(student.get("progress") or {}).get("completed", 0)');
  } else {
    return null;
  }
  if (after === before) return null;
  lines[lineNumber - 1] = after;
  return {
    before,
    after,
    content: lines.join('\n'),
    explanation: 'A student can have no progress record yet. Return zero completed lessons in that case.'
  };
}
