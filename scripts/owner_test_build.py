"""Private local owner QA EXE, isolated from customer release artifacts."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from clearpage.billing.licensing import configuration, payment_ready
from scripts.release import version, collect_licenses
from scripts.standard_smoke import check_package


def profile_policy(profile):
    """Resolve a private QA profile at runtime without embedding a home username."""
    relative = Path(profile).resolve().relative_to(Path.home().resolve())
    return ('import os\nos.environ["CLEARPAGE_PAYMENT_LOCAL_TEST"] = "1"\n'
            'os.environ["CLEARPAGE_DATA_DIR"] = os.path.join(os.path.expanduser("~"), '
            + repr(str(relative)) + ')\n')


def run(config,profile,dist,work,output):
    config,profile,dist,work,output=(Path(p).resolve() for p in (config,profile,dist,work,output))
    if config.is_relative_to(ROOT) or profile.is_relative_to(ROOT) or config.name!='billing.json':
        raise ValueError('Use a separate private test folder and profile outside the repository.')
    if os.environ.get('RELEASE_TAG') or output.exists() or (dist/'ClearPage').exists():
        raise ValueError('Owner tests cannot be version-tagged or overwrite an existing build.')
    profile_policy(profile)
    value=version()
    with_config=configuration(config)
    if not with_config or with_config.get('payment_provider')!='razorpay' or with_config.get('payment_service_url')!='http://127.0.0.1:5056':
        raise ValueError('Only the dedicated localhost test service is allowed in this private build.')
    os.environ['CLEARPAGE_PAYMENT_LOCAL_TEST']='1'
    if not payment_ready(with_config):raise ValueError('Complete the public local TEST configuration first.')
    env={**os.environ,'CLEARPAGE_BUILD_STANDARD':'1','CLEARPAGE_BUILD_PREVIEW':'0',
        'CLEARPAGE_BUILD_OWNER_TEST':'1','CLEARPAGE_BUILD_OWNER_TEST_CONFIG':str(config),
        'CLEARPAGE_BUILD_OWNER_TEST_PROFILE':str(profile)}
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--distpath',str(dist),
        '--workpath',str(work),'ClearPage.spec'],cwd=ROOT,env=env,check=True)
    bundle=dist/'ClearPage'
    collect_licenses(bundle,standard=True)
    checks=check_package(bundle,value)
    (bundle/'START-HERE.txt').write_text('''ClearPage — Private Owner TEST Application

Double-click ClearPage.exe. No Python command or browser tab is required for
document work. Keep _internal beside the EXE. This local QA application connects
only to the separate TEST service on 127.0.0.1:5056 and uses your existing test
customer profile. Start that service first; owner admin login is still required.

Your Razorpay secrets, admin password/hash, signing private key and database are
NOT bundled. This is not a public release and cannot use the release publisher.
Windows 10/11 x64 and Microsoft Edge WebView2 Runtime are required. The EXE has
ClearPage branding and Windows product metadata, but remains unsigned. Finish
exporting work in the old app before switching, and sign in as admin again.
''',encoding='utf-8')
    shutil.copytree(bundle,output)
    report=dict(version=value,owner_test=True,private_local_only=True,publish_allowed=False,
        checks=checks,branding='ClearPage icon, Windows product/version resources and application identity',
        publisher_signature='unsigned',output=output.name)
    (output.parent/'Owner-test-build-checks.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for flag in ('config','profile','dist','work','output'):parser.add_argument('--'+flag,required=True,type=Path)
    args=parser.parse_args();run(args.config,args.profile,args.dist,args.work,args.output)
