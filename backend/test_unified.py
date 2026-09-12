from starlette.testclient import TestClient
from app.main import app

client = TestClient(app, raise_server_exceptions=False)

# 1. Root HTML
r = client.get('/', headers={'accept': 'text/html'})
assert r.status_code == 200 and '<div id="root">' in r.text, f'Root failed: {r.status_code}'
print('PASS 1: GET / (SPA index.html)')

# 2. Browser navigation to SPA route /applications
r = client.get('/applications', headers={'accept': 'text/html,application/xhtml+xml'})
assert r.status_code == 200 and '<div id="root">' in r.text, f'SPA route failed: {r.status_code}'
print('PASS 2: GET /applications (SPA HTML)')

# 3. Browser navigation to /dashboard
r = client.get('/dashboard', headers={'accept': 'text/html'})
assert r.status_code == 200 and '<div id="root">' in r.text, f'Dashboard route failed: {r.status_code}'
print('PASS 3: GET /dashboard (SPA HTML)')

# 4. Health check
r = client.get('/health')
assert r.status_code == 200 and r.json() == {'status': 'healthy'}, f'Health failed: {r.text}'
print('PASS 4: GET /health')

# 5. Static JS
r = client.get('/static/js/main.155833fd.js')
assert r.status_code == 200, f'Static JS failed: {r.status_code}'
print('PASS 5: GET /static/js/... (200 OK)')

# 6. Swagger docs
r = client.get('/api/docs')
assert r.status_code == 200, f'API docs failed: {r.status_code}'
print('PASS 6: GET /api/docs (200 OK)')

# 7. Auth login
r = client.post('/auth/login', json={'email': 'admin@smartverify.com', 'password': 'admin123'})
assert r.status_code == 200 and 'access_token' in r.json(), f'Auth failed: {r.text}'
token = r.json()['access_token']
print('PASS 7: POST /auth/login (JWT returned)')

# 8. API call to /applications/ with JWT and application/json
r = client.get('/applications/', headers={'Authorization': f'Bearer {token}', 'accept': 'application/json'})
assert r.status_code == 200 and isinstance(r.json(), list), f'API applications failed: {r.text}'
print(f'PASS 8: GET /applications/ (API returned {len(r.json())} applications)')

# 9. API call to /dashboard/stats
r = client.get('/dashboard/stats', headers={'Authorization': f'Bearer {token}'})
assert r.status_code == 200, f'Dashboard stats failed: {r.text}'
print('PASS 9: GET /dashboard/stats (API returned stats)')

print('\nALL 9 TESTS PASSED PERFECTLY!')
