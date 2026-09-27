import http from 'node:http';

const students = {
  '42': { name: 'Aarav', progress: undefined },
  '43': { name: 'Diya', progress: { completed: 8 } }
};

export function getStudentProgress(id) {
  const student = students[id];
  if (!student) return null;
  return { id, name: student.name, completed: student.progress.completed };
}

export function startDemoApi(port = 4101) {
  const server = http.createServer((request, response) => {
    const match = new URL(request.url, 'http://localhost').pathname.match(/^\/api\/students\/(\d+)\/progress$/);
    if (!match) {
      response.writeHead(404, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ error: 'Not found' }));
      return;
    }
    try {
      const result = getStudentProgress(match[1]);
      response.writeHead(result ? 200 : 404, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(result ?? { error: 'Student not found' }));
    } catch (error) {
      response.writeHead(500, { 'Content-Type': 'application/json' });
      // The demo returns a stack trace to simulate a connected log stream.
      response.end(JSON.stringify({ error: 'Demo API crashed', stack: error.stack }));
    }
  });
  return new Promise(resolve => server.listen(port, '127.0.0.1', () => resolve(server)));
}
