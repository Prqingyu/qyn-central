// 中控机出口诊断：DNS / TCP / TLS 三层分开看，别混成一个 "Connection error"。
const https = require('https');
const dns = require('dns');
const net = require('net');

const HOST = process.argv[2] || 'opencode.ai';
const PATH = process.argv[3] || '/zen/go/v1/models';

function probe(name, fn) {
  return new Promise((resolve) => {
    const t = setTimeout(() => { console.log(`${name}: TIMEOUT(10s)`); resolve(); }, 10000);
    fn((msg) => { clearTimeout(t); console.log(`${name}: ${msg}`); resolve(); });
  });
}

(async () => {
  await probe('1 DNS(A)', (done) => dns.resolve4(HOST, (e, a) => done(e ? `ERR ${e.code}` : a.join(','))));
  await probe('2 DNS(AAAA)', (done) => dns.resolve6(HOST, (e, a) => done(e ? `ERR ${e.code}` : a.join(','))));
  const ip = await new Promise((r) => dns.resolve4(HOST, (e, a) => r(e ? null : a[0])));
  await probe(`3 TCP ${ip}:443`, (done) => {
    const s = net.connect({ host: ip, port: 443, timeout: 8000 });
    s.on('connect', () => { done('OK'); s.destroy(); });
    s.on('error', (e) => done(`ERR ${e.code}`));
    s.on('timeout', () => { done('TIMEOUT'); s.destroy(); });
  });
  await probe(`4 HTTPS ${HOST}${PATH}`, (done) => {
    const req = https.get({ host: HOST, path: PATH, timeout: 10000, family: 4 }, (res) => {
      done(`HTTP ${res.statusCode}`); res.destroy();
    });
    req.on('error', (e) => done(`ERR ${e.code || e.message}`));
    req.on('timeout', () => { done('TIMEOUT'); req.destroy(); });
  });
  await probe('5 HTTPS 对照 www.baidu.com', (done) => {
    const req = https.get({ host: 'www.baidu.com', path: '/', timeout: 10000, family: 4 }, (res) => {
      done(`HTTP ${res.statusCode}`); res.destroy();
    });
    req.on('error', (e) => done(`ERR ${e.code || e.message}`));
    req.on('timeout', () => { done('TIMEOUT'); req.destroy(); });
  });
})();
