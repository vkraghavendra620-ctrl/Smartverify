import sys, os, sqlite3, json
backend_dir = os.path.abspath('backend')
sys.path.insert(0, backend_dir)

from app.services.parsers.pan_parser import parse_pan, normalize_pan_token, extract_pan_candidates
from app.services.parsers.aadhaar_parser import parse_aadhaar
from app.services.nlp_service import extract_information

print('--- Test 1: Character Confusion Normalization ---')
cases = [
    ('BRCPY144OA', 'BRCPY1440A', 'O -> 0'),
    ('BRCPYI440A', 'BRCPY1440A', 'I -> 1'),
    ('BRCPYl440A', 'BRCPY1440A', 'l -> 1'),
    ('BRCPY144SA', 'BRCPY1445A', 'S -> 5'),
    ('BRCPY144BA', 'BRCPY1448A', 'B -> 8'),
    ('BRCPY144ZA', 'BRCPY1442A', 'Z -> 2'),
    ('0RCPY1440A', 'ORCPY1440A', '0 -> O in letter pos'),
    ('1RCPY1440A', 'IRCPY1440A', '1 -> I in letter pos'),
    ('BRCPY1440A', 'BRCPY1440A', 'Exact clean match'),
]
for raw, expected, desc in cases:
    res = normalize_pan_token(raw)
    assert res is not None, 'Failed to normalize: ' + raw
    assert res[0] == expected, 'Expected ' + expected + ' for ' + raw + ', got ' + res[0]
    print('  [PASS] ' + raw + ' -> ' + res[0] + ' (' + desc + ', corrections=' + str(res[1]) + ')')

print('\n--- Test 2: Spaced and Punctuated PAN Formats ---')
cases = [
    ('B R C P Y 1 4 4 0 A', 'BRCPY1440A'),
    ('BRCPY-1440-A', 'BRCPY1440A'),
    ('BRCPY 1440A', 'BRCPY1440A'),
    ('BRCPY 1440 A', 'BRCPY1440A'),
    ('PAN NO: BRCPY144OA', 'BRCPY1440A'),
]
for raw, expected in cases:
    cands = extract_pan_candidates(raw)
    assert len(cands) > 0, 'No candidates found for: ' + raw
    cand_val = cands[0]['pan']
    assert cand_val == expected, 'Expected ' + expected + ', got ' + cand_val
    print('  [PASS] ' + raw + ' -> ' + cand_val)

print('\n--- Test 3: Real Database Document 11 (Real PAN Card) ---')
db_path = os.path.join(backend_dir, 'smartverify.db')
conn = sqlite3.connect(db_path)
c = conn.cursor()
c.execute('SELECT extracted_text FROM documents WHERE id=11')
row = c.fetchone()
conn.close()

assert row and row[0], 'Document 11 text not found in smartverify.db'
raw_text = row[0]
res = parse_pan(raw_text)

pan_num = res['pan_number']['value']
app_name = res['applicant_name']['value']
f_name = res['father_name']['value']
dob_val = res['dob']['value']

assert pan_num == 'BRCPY1440A', 'PAN mismatch: ' + str(pan_num)
assert app_name == 'RENIKA YATHISH', 'Name mismatch: ' + str(app_name)
assert f_name == 'YATHISH', 'Father mismatch: ' + str(f_name)
assert dob_val == '26/12/2005', 'DOB mismatch: ' + str(dob_val)

print('  [PASS] PAN Number:     ' + str(pan_num))
print('  [PASS] Applicant Name: ' + str(app_name))
print('  [PASS] Father Name:    ' + str(f_name))
print('  [PASS] Date of Birth:  ' + str(dob_val))

print('\n--- Test 4: Aadhaar OCR Regression Check ---')
sample_aadhaar_text = 'GOVERNMENT OF INDIA\nV K Raghavendra\nDOB: 15/08/1990\nMale\n9876 5432 1098'
res_aadhaar = parse_aadhaar(sample_aadhaar_text)

a_num = res_aadhaar['aadhaar_number']['value']
a_name = res_aadhaar['applicant_name']['value']

assert a_num == '987654321098', 'Aadhaar failed: ' + str(a_num)
assert a_name == 'V K Raghavendra', 'Aadhaar name failed: ' + str(a_name)
print('  [PASS] Aadhaar Number: ' + str(a_num))
print('  [PASS] Aadhaar Name:   ' + str(a_name))

print('\nALL PAN & AADHAAR OCR TESTS PASSED 100%!')
