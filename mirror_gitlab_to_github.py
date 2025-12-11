import os
import subprocess
import sys
import time
import requests

GITLAB_URL = os.environ.get("GITLAB_URL", "").rstrip("/")
GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN")
GITHUB_ORG = os.environ.get("GITHUB_ORG")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
CLONE_BASE = os.environ.get("CLONE_BASE", "./gitlab-mirror")

if not all([GITLAB_URL, GITLAB_TOKEN, GITHUB_ORG, GITHUB_TOKEN]):
    print("Missing one of GITLAB_URL, GITLAB_TOKEN, GITHUB_ORG, GITHUB_TOKEN env vars")
    sys.exit(1)

os.makedirs(CLONE_BASE, exist_ok=True)

# --- helpers ---------------------------------------------------------

def run(cmd, cwd=None):
    print(f"\n>> {cmd}")
    result = subprocess.run(cmd, cwd=cwd, shell=True)
    if result.returncode != 0:
        print(f"Command failed with code {result.returncode}")
    return result.returncode

def gitlab_get(path, params=None):
    headers = {"PRIVATE-TOKEN": GITLAB_TOKEN}
    url = f"{GITLAB_URL}/api/v4{path}"
    r = requests.get(url, headers=headers, params=params or {})
    r.raise_for_status()
    return r

def list_all_gitlab_projects():
    projects = []
    page = 1
    per_page = 100
    while True:
        r = gitlab_get("/projects", params={
            "simple": True,
            "membership": False,   # change to True if you want only your memberships
            "per_page": per_page,
            "page": page,
            "order_by": "id",
            "sort": "asc"
        })
        batch = r.json()
        if not batch:
            break
        projects.extend(batch)
        if len(batch) < per_page:
            break
        page += 1
    return projects

def create_github_repo(name, description="", private=True):
    url = "https://api.github.com/orgs/{org}/repos".format(org=GITHUB_ORG)
    # if you want repos under a user instead of org, use: https://api.github.com/user/repos
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    payload = {
        "name": name,
        "description": description,
        "private": private,
        "auto_init": False
    }
    r = requests.post(url, headers=headers, json=payload)
    if r.status_code == 422 and "name already exists" in r.text:
        print(f"GitHub repo {name} already exists, reusing.")
        return f"https://github.com/{GITHUB_ORG}/{name}.git"
    r.raise_for_status()
    data = r.json()
    return data["clone_url"]

# --- main logic ------------------------------------------------------

def main():
    projects = list_all_gitlab_projects()
    print(f"Found {len(projects)} GitLab projects")

    for p in projects:
        full_path = p["path_with_namespace"]      # e.g. group/subgroup/project
        proj_name = p["path"]                     # leaf name only
        http_url = p["http_url_to_repo"]          # HTTPS clone URL

        # Optional: skip archived or forks
        if p.get("archived"):
            print(f"Skipping archived project {full_path}")
            continue

        print(f"\n=== Processing {full_path} ===")

        # 1) clone bare mirror from GitLab
        local_dir = os.path.join(CLONE_BASE, full_path.replace("/", "__") + ".git")
        if not os.path.exists(local_dir):
            os.makedirs(os.path.dirname(local_dir), exist_ok=True)
            clone_cmd = (
                f"git -c http.sslVerify=false clone --mirror \"{http_url}\" \"{local_dir}\""
            )
            if run(clone_cmd) != 0:
                print(f"Skipping {full_path} due to clone error")
                continue
        else:
            print(f"Local mirror exists for {full_path}, fetching updates")
            if run("git fetch --all --prune", cwd=local_dir) != 0:
                print(f"Skipping {full_path} due to fetch error")
                continue

        # 2) create GitHub repo
        gh_repo_name = proj_name    # or customize naming scheme here
        try:
            gh_url = create_github_repo(
                gh_repo_name,
                description=p.get("description") or f"Mirror of {full_path} from {GITLAB_URL}",
                private=not p.get("public", False),
            )
        except Exception as e:
            print(f"Failed to create GitHub repo for {full_path}: {e}")
            continue

        print(f"GitHub repo URL: {gh_url}")

        # 3) add GitHub remote and push --mirror
        # remove existing 'github' remote if rerun
        run("git remote remove github", cwd=local_dir)
        if run(f"git remote add github \"{gh_url}\"", cwd=local_dir) != 0:
            print(f"Failed to add github remote for {full_path}")
            continue

        if run("git push --mirror github", cwd=local_dir) != 0:
            print(f"Mirror push failed for {full_path}")
            continue

        # small delay to avoid API/rate issues
        time.sleep(1)

if __name__ == "__main__":
    main()
