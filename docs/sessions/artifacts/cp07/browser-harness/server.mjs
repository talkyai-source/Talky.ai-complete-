import http from 'node:http';
import {readFile} from 'node:fs/promises';
import {dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
const directory = dirname(fileURLToPath(import.meta.url));
http.createServer(async (request,response) => {
  if (request.method !== 'GET') { response.writeHead(405).end(); return; }
  const pathname = new URL(request.url,'http://127.0.0.1').pathname;
  const name = pathname === '/bundle.js' ? 'bundle.js' : pathname === '/' ? 'index.html' : null;
  if (!name) { response.writeHead(404).end(); return; }
  try { response.writeHead(200,{'content-type':name.endsWith('.js')?'text/javascript':'text/html','cache-control':'no-store'}).end(await readFile(join(directory,name))); }
  catch { response.writeHead(500).end(); }
}).listen(3197,'127.0.0.1',()=>process.stdout.write(JSON.stringify({ready:true,pid:process.pid,url:'http://127.0.0.1:3197'})+'\n'));
