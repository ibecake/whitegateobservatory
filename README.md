# Whitegate Observatory Dashboard Deployment

This project is a static dashboard (single-page `index.html`) that can be hosted from any static web server or GitHub Pages site. The HTML, CSS, and JavaScript assets live in this repository, so any edits made in this CodeX container (or on your own machine) must be published to your remote repository (e.g., GitHub) before they will appear on your live site.

## Updating the Live Site

1. **Commit your changes in the container**
   ```bash
   git status
   git add index.html
   git commit -m "Update dashboard"
   ```
2. **Authenticate (one time per session, if prompted)**
   If this environment has not yet authenticated with GitHub, create a [personal access token](https://github.com/settings/tokens) (classic) that has `repo` scope. When Git asks for a password during `git push`, paste the token instead of your GitHub password.

3. **Push to GitHub**
   ```bash
   git push origin <branch-name>
   ```
   Replace `<branch-name>` with the branch that your site is published from (commonly `main` or `master`). The push command works from inside this CodeX container as long as the token step above is completed.
4. **Trigger your hosting provider**
   - **GitHub Pages**: pushing to the configured branch automatically redeploys the site. Wait a few minutes, then refresh `https://whitegateobservatory.com`.
   - **Other hosts**: follow their deployment instructions (for example, upload the updated `index.html` via FTP or trigger a CI/CD pipeline).

If you do not push your latest commit to the branch your hosting provider uses, the live site will not update.

## Verifying Deployment

After pushing, clear your browser cache or use a private/incognito window to ensure you are loading the updated assets. You can also inspect the page source in the browser and confirm that it matches the latest `index.html` content from your repository.

## Troubleshooting

- **Changes not visible after push**: confirm GitHub Actions or Pages build logs for errors.
- **Cached content**: perform a hard refresh (`Ctrl+Shift+R` / `Cmd+Shift+R`).
- **DNS propagation**: if you changed DNS settings, allow time for propagation.

For additional deployment automation, consider configuring a CI workflow that builds and publishes the site whenever changes are merged into the deployment branch.

## API Keys and Secrets

Do not store live API keys in tracked source files.

Use environment variables instead:

```bash
export METEOSOURCE_API_KEY="your-real-key"
export WORLD_TIDES_KEY="your-real-key"
```

For local convenience, use an untracked `.env` file:

1. Copy [.env.example](.env.example) to `.env`.
2. Fill in real values in `.env`.
3. Load it in your shell before running builders:

```bash
set -a
source .env
set +a
```

The repository includes [.gitignore](.gitignore) rules to keep `.env` files out of git.
