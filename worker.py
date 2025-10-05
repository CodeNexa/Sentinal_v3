import os, json, uuid, shutil, requests, traceback, time
from jinja2 import Environment, FileSystemLoader
from dotenv import load_dotenv
import boto3

load_dotenv()
STORAGE_DIR = os.getenv('STORAGE_DIR','/storage')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
OPENAI_MODEL = os.getenv('OPENAI_MODEL','gpt-4o-mini')
S3_ENDPOINT = os.getenv('S3_ENDPOINT')
S3_BUCKET = os.getenv('S3_BUCKET')

env = Environment(loader=FileSystemLoader('/templates'))

def call_llm(idea, template):
    if not OPENAI_API_KEY:
        return None
    prompt = f"Create a JSON mapping file paths to contents for a small project based on: {idea}\nTemplate: {template}"
    headers = {'Authorization': f'Bearer {OPENAI_API_KEY}','Content-Type':'application/json'}
    data = {'model': OPENAI_MODEL, 'messages':[{'role':'user','content':prompt}], 'temperature':0.2}
    try:
        r = requests.post('https://api.openai.com/v1/chat/completions', json=data, headers=headers, timeout=60)
        r.raise_for_status()
        text = r.json()['choices'][0]['message']['content']
        import re
        m = re.search(r'\{[\s\S]*\}', text)
        if not m: return None
        return json.loads(m.group(0))
    except Exception as e:
        print('LLM error',e)
        return None

def write_files(root, files):
    for path, content in files.items():
        fp = os.path.join(root, path)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, 'w', encoding='utf-8') as f:
            f.write(content)

def zip_folder(folder, zipname):
    shutil.make_archive(zipname.replace('.zip',''), 'zip', folder)

def upload_s3(zip_path, key):
    if not S3_BUCKET:
        return None
    s3 = boto3.client('s3', endpoint_url=S3_ENDPOINT) if S3_ENDPOINT else boto3.client('s3')
    s3.upload_file(zip_path, S3_BUCKET, key)
    return key

def process_job(payload):
    job_id = payload.get('_job_id', str(uuid.uuid4()))
    name = payload.get('name','project-'+str(int(time.time())))
    idea = payload.get('idea','')
    template = payload.get('template','python-cli')
    print('Starting job',job_id,name)
    try:
        out = os.path.join(STORAGE_DIR, job_id)
        os.makedirs(out, exist_ok=True)
        generated = call_llm(idea, template)
        if generated:
            write_files(out, generated)
        else:
            # fallback simple files
            tmpl = env.get_template('python-cli/README.tpl')
            open(os.path.join(out,'README.md'),'w').write(tmpl.render(project_name=name, idea=idea))
            main = env.get_template('python-cli/main.tpl')
            open(os.path.join(out,'main.py'),'w').write(main.render(project_name=name, idea=idea))
        zip_path = os.path.join(STORAGE_DIR, f"{job_id}.zip")
        zip_folder(out, zip_path)
        s3key = upload_s3(zip_path, f"{job_id}.zip")
        print('Job done', job_id, 's3:', s3key)
        return {'job_id': job_id, 'zip': zip_path, 's3_key': s3key}
    except Exception as e:
        print('job failed', e, traceback.format_exc())
        raise
