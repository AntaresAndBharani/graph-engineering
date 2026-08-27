<#
.SYNOPSIS
    Generates and refreshes the GitHub Repositories HTML Directory.

.DESCRIPTION
    Queries GitHub CLI for all repositories under AntaresAndBharani (and Antares1980),
    fetches live open story counts, PRs, and action status, and renders the HTML dashboard
    at docs/github-repositories-directory.html.

.EXAMPLE
    .\scripts\export-github-directory.ps1
#>
param(
    [string]$OutputPath = (Join-Path $PSScriptRoot "..\docs\github-repositories-directory.html"),
    [string[]]$Orgs = @("AntaresAndBharani", "Antares1980")
)

$ErrorActionPreference = "Continue"

# Load GitHub Auth Token
$ghTokenScript = "C:\Users\rogal\workspaces\Set-GhToken-Antares.ps1"
if (Test-Path -LiteralPath $ghTokenScript) {
    try { & $ghTokenScript | Out-Null } catch {}
}

Write-Host "Fetching repositories from GitHub..." -ForegroundColor Cyan

$allRepos = @()
foreach ($org in $Orgs) {
    try {
        $raw = gh repo list $org --limit 100 --json name,nameWithOwner,description,isPrivate,isFork,url,pushedAt,defaultBranchRef 2>$null
        if ($raw) {
            $repos = $raw | ConvertFrom-Json
            $allRepos += $repos
        }
    } catch {
        Write-Warning "Could not fetch repos for $($org): $($_.ToString())"
    }
}

Write-Host "Discovered $($allRepos.Count) repositories. Generating HTML..." -ForegroundColor Green

# Prepare JSON data for client-side embedding
$reposJson = $allRepos | ConvertTo-Json -Depth 5 -Compress

$generatedTime = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")

$html = @"
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GitHub Repositories Directory & Live Status Dashboard</title>
    <style>
        :root {
            --bg-primary: #0d1117;
            --bg-secondary: #161b22;
            --bg-tertiary: #21262d;
            --border-color: #30363d;
            --text-primary: #c9d1d9;
            --text-secondary: #8b949e;
            --text-white: #f0f6fc;
            --accent-blue: #58a6ff;
            --accent-blue-bg: rgba(56, 139, 253, 0.15);
            --accent-purple: #bc8cff;
            --accent-purple-bg: rgba(188, 140, 255, 0.15);
            --accent-green: #3fb950;
            --accent-green-bg: rgba(63, 185, 80, 0.15);
            --accent-orange: #d29922;
            --accent-orange-bg: rgba(210, 153, 34, 0.15);
            --accent-cyan: #39c5cf;
            --accent-cyan-bg: rgba(57, 197, 207, 0.15);
            --accent-red: #f85149;
            --accent-red-bg: rgba(248, 81, 73, 0.15);
            --radius-md: 8px;
            --radius-sm: 6px;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif;
        }

        body {
            background-color: var(--bg-primary);
            color: var(--text-primary);
            padding: 32px 24px;
            line-height: 1.5;
        }

        .container {
            max-width: 1350px;
            margin: 0 auto;
        }

        header {
            margin-bottom: 24px;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 20px;
        }

        .header-top {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
        }

        h1 {
            color: var(--text-white);
            font-size: 24px;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .badge-org {
            background: var(--accent-blue-bg);
            color: var(--accent-blue);
            border: 1px solid rgba(56, 139, 253, 0.4);
            font-size: 13px;
            font-weight: 500;
            padding: 3px 10px;
            border-radius: 20px;
        }

        p.subtitle {
            color: var(--text-secondary);
            font-size: 13px;
            margin-top: 6px;
        }

        /* Action Toolbar */
        .toolbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
            margin-top: 18px;
            padding: 12px 16px;
            background-color: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-md);
        }

        .btn-action-group {
            display: flex;
            gap: 10px;
            align-items: center;
            flex-wrap: wrap;
        }

        .btn-tool {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 8px 14px;
            border-radius: var(--radius-md);
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            border: 1px solid var(--border-color);
            background-color: var(--bg-tertiary);
            color: var(--text-white);
            transition: all 0.2s;
        }

        .btn-tool:hover {
            border-color: var(--accent-blue);
            background-color: #30363d;
        }

        .btn-tool.primary {
            background-color: #238636;
            border-color: rgba(240, 246, 252, 0.1);
        }

        .btn-tool.primary:hover {
            background-color: #2ea043;
        }

        .token-input {
            display: flex;
            align-items: center;
            gap: 6px;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-sm);
            padding: 4px 8px;
        }

        .token-input input {
            background: transparent;
            border: none;
            color: var(--text-white);
            font-size: 12px;
            outline: none;
            width: 140px;
        }

        /* Controls bar */
        .controls {
            display: flex;
            gap: 14px;
            margin-top: 16px;
            flex-wrap: wrap;
        }

        .search-box {
            flex: 1;
            min-width: 260px;
            position: relative;
        }

        .search-box input {
            width: 100%;
            background-color: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-md);
            padding: 10px 16px 10px 38px;
            color: var(--text-white);
            font-size: 14px;
            outline: none;
            transition: border-color 0.2s, box-shadow 0.2s;
        }

        .search-box input:focus {
            border-color: var(--accent-blue);
            box-shadow: 0 0 0 3px rgba(88, 166, 255, 0.2);
        }

        .search-icon {
            position: absolute;
            left: 12px;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-secondary);
            pointer-events: none;
        }

        .filter-tags {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            align-items: center;
        }

        .filter-btn {
            background-color: var(--bg-secondary);
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            padding: 8px 14px;
            border-radius: var(--radius-md);
            font-size: 13px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s;
        }

        .filter-btn:hover, .filter-btn.active {
            background-color: var(--bg-tertiary);
            color: var(--text-white);
            border-color: var(--accent-blue);
        }

        /* Status notification bar */
        #statusBar {
            margin-top: 14px;
            padding: 8px 14px;
            border-radius: var(--radius-sm);
            font-size: 12px;
            display: none;
        }

        #statusBar.info {
            display: block;
            background: var(--accent-blue-bg);
            color: var(--accent-blue);
            border: 1px solid rgba(88, 166, 255, 0.3);
        }

        #statusBar.success {
            display: block;
            background: var(--accent-green-bg);
            color: var(--accent-green);
            border: 1px solid rgba(63, 185, 80, 0.3);
        }

        /* Repos Grid */
        .repo-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(410px, 1fr));
            gap: 18px;
            margin-top: 20px;
        }

        .repo-card {
            background-color: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-md);
            padding: 18px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            transition: transform 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
        }

        .repo-card:hover {
            border-color: #58a6ff;
            transform: translateY(-2px);
            box-shadow: 0 8px 24px rgba(0,0,0,0.3);
        }

        .repo-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 10px;
            margin-bottom: 8px;
        }

        .repo-title-area {
            display: flex;
            flex-direction: column;
            gap: 2px;
        }

        .repo-name-link {
            color: var(--accent-blue);
            font-size: 16px;
            font-weight: 600;
            text-decoration: none;
            word-break: break-word;
        }

        .repo-name-link:hover {
            text-decoration: underline;
        }

        .repo-full-name {
            font-size: 11px;
            color: var(--text-secondary);
        }

        .visibility-tag {
            font-size: 11px;
            font-weight: 500;
            padding: 2px 8px;
            border-radius: 12px;
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            white-space: nowrap;
        }

        .repo-desc {
            font-size: 13px;
            color: var(--text-secondary);
            margin: 8px 0 14px 0;
            min-height: 38px;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        /* Live Status Chips */
        .live-status-row {
            display: flex;
            gap: 8px;
            margin-bottom: 14px;
            flex-wrap: wrap;
        }

        .status-chip {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
            background: var(--bg-tertiary);
            color: var(--text-secondary);
            border: 1px solid var(--border-color);
        }

        .status-chip.green {
            background: var(--accent-green-bg);
            color: var(--accent-green);
            border-color: rgba(63, 185, 80, 0.4);
        }

        .status-chip.orange {
            background: var(--accent-orange-bg);
            color: var(--accent-orange);
            border-color: rgba(210, 153, 34, 0.4);
        }

        .status-chip.red {
            background: var(--accent-red-bg);
            color: var(--accent-red);
            border-color: rgba(248, 81, 73, 0.4);
        }

        .status-chip.purple {
            background: var(--accent-purple-bg);
            color: var(--accent-purple);
            border-color: rgba(188, 140, 255, 0.4);
        }

        /* Action Buttons / Links */
        .link-group {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 8px;
            margin-top: auto;
        }

        .btn-link {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            padding: 8px 10px;
            border-radius: var(--radius-sm);
            font-size: 12px;
            font-weight: 600;
            text-decoration: none;
            transition: all 0.15s ease;
            text-align: center;
        }

        .btn-story {
            background-color: var(--accent-purple-bg);
            color: var(--accent-purple);
            border: 1px solid rgba(188, 140, 255, 0.4);
            grid-column: span 2;
            padding: 9px 12px;
            font-size: 13px;
        }

        .btn-story:hover {
            background-color: rgba(188, 140, 255, 0.25);
            border-color: var(--accent-purple);
        }

        .btn-pr {
            background-color: var(--accent-green-bg);
            color: var(--accent-green);
            border: 1px solid rgba(63, 185, 80, 0.4);
        }

        .btn-pr:hover {
            background-color: rgba(63, 185, 80, 0.25);
            border-color: var(--accent-green);
        }

        .btn-actions {
            background-color: var(--accent-orange-bg);
            color: var(--accent-orange);
            border: 1px solid rgba(210, 153, 34, 0.4);
        }

        .btn-actions:hover {
            background-color: rgba(210, 153, 34, 0.25);
            border-color: var(--accent-orange);
        }

        .section-heading {
            color: var(--text-white);
            font-size: 17px;
            font-weight: 600;
            margin: 28px 0 14px 0;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .section-badge {
            font-size: 11px;
            background: var(--bg-tertiary);
            color: var(--text-secondary);
            padding: 2px 8px;
            border-radius: 12px;
            font-weight: 500;
        }

        .icon {
            width: 15px;
            height: 15px;
            fill: currentColor;
            display: inline-block;
            vertical-align: middle;
        }

        footer {
            margin-top: 48px;
            text-align: center;
            font-size: 12px;
            color: var(--text-secondary);
            border-top: 1px solid var(--border-color);
            padding-top: 20px;
        }
    </style>
</head>
<body>

<div class="container">
    <header>
        <div class="header-top">
            <h1>
                <svg class="icon" style="width: 26px; height: 26px;" viewBox="0 0 16 16" fill="currentColor">
                    <path d="M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 0 1-5.45 7.59c-.4.08-.55-.17-.55-.38 0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-1.59-.82-2.15.08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.73.9.82 1.13.16.45.68 1.31 2.69.94 0 .67.01 1.3.01 1.49 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0-4.42 3.58-8 8-8Z"></path>
                </svg>
                GitHub Repositories Directory & Live Status
                <span class="badge-org">AntaresAndBharani & Antares1980</span>
            </h1>
            <div style="font-size: 12px; color: var(--text-secondary);">
                Snapshot Generated: <code>$generatedTime</code>
            </div>
        </div>
        <p class="subtitle">Direct access to <strong>OPEN User Stories (`is:open label:type:user-story`)</strong>, <strong>Pull Requests</strong>, and <strong>GitHub Actions CI/CD</strong>.</p>

        <!-- Live Action Toolbar -->
        <div class="toolbar">
            <div class="btn-action-group">
                <button class="btn-tool primary" onclick="refreshStatusFromGitHub()" id="btnLiveStatus">
                    ⚡ Refresh Status in GitHub.com
                </button>
                <button class="btn-tool" onclick="refreshLocalView()">
                    🔄 Refresh Directory View
                </button>
            </div>
            <div class="btn-action-group">
                <div class="token-input" title="Optional GitHub Personal Access Token to increase API rate limits">
                    <span style="font-size: 11px; color: var(--text-secondary);">GH Token:</span>
                    <input type="password" id="ghTokenInput" placeholder="Optional PAT..." onchange="saveGhToken(this.value)">
                </div>
            </div>
        </div>

        <div id="statusBar"></div>

        <div class="controls">
            <div class="search-box">
                <span class="search-icon">🔍</span>
                <input type="text" id="searchInput" placeholder="Filter repositories by name, description, or owner..." onkeyup="filterRepos()">
            </div>
            <div class="filter-tags">
                <button class="filter-btn active" onclick="setCategoryFilter('all', this)">All</button>
                <button class="filter-btn" onclick="setCategoryFilter('sdlc', this)">Graph SDLC</button>
                <button class="filter-btn" onclick="setCategoryFilter('trading', this)">Trading & Quant</button>
                <button class="filter-btn" onclick="setCategoryFilter('fitness', this)">Gym & Fitness</button>
                <button class="filter-btn" onclick="setCategoryFilter('tools', this)">Tooling & Infra</button>
            </div>
        </div>
    </header>

    <div id="repositoriesContainer"></div>

    <footer>
        Graph Engineering &bull; GitHub Ecosystem Live Directory &bull; Direct SDLC Access Portal.
    </footer>
</div>

<script>
    const repositoriesData = $reposJson;

    // Load saved GitHub token
    document.addEventListener('DOMContentLoaded', () => {
        const savedToken = localStorage.getItem('gh_pat_token');
        if (savedToken) {
            document.getElementById('ghTokenInput').value = savedToken;
        }
        renderRepositories(repositoriesData);
    });

    function saveGhToken(token) {
        if (token) {
            localStorage.setItem('gh_pat_token', token.trim());
            showStatus('GitHub PAT token saved locally for live queries.', 'success');
        } else {
            localStorage.removeItem('gh_pat_token');
            showStatus('GitHub PAT token cleared.', 'info');
        }
    }

    function showStatus(message, type = 'info') {
        const bar = document.getElementById('statusBar');
        bar.className = type;
        bar.textContent = message;
        bar.style.display = 'block';
    }

    function categorizeRepo(repo) {
        const name = repo.name.toLowerCase();
        const desc = (repo.description || '').toLowerCase();
        const categories = [];

        if (name.includes('crosstraining') || name.includes('darwin') || name.includes('stock-manager') || name.includes('graph-engineering') || name.includes('gh-development') || name.includes('virgymia-qa')) {
            categories.push('sdlc');
        }
        if (name.includes('trader') || name.includes('stock') || name.includes('trading') || name.includes('crypto') || name.includes('broker') || name.includes('backtrader')) {
            categories.push('trading');
        }
        if (name.includes('gym') || name.includes('fitness') || name.includes('crosstraining') || name.includes('virgymia') || name.includes('qgeneration')) {
            categories.push('fitness');
        }
        if (name.includes('template') || name.includes('dotfiles') || name.includes('github') || name.includes('bdd') || name.includes('api-rest') || name.includes('tools')) {
            categories.push('tools');
        }
        if (categories.length === 0) categories.push('tools');
        return categories.join(' ');
    }

    function renderRepositories(repos) {
        const container = document.getElementById('repositoriesContainer');
        container.innerHTML = '';

        // Group into sections
        const sections = [
            { id: 'sdlc', title: '🚀 Core Graph Engineering & SDLC Pipelines', badge: 'Active SDLC Graphs' },
            { id: 'trading', title: '📈 Trading, Quant & Financial Microservices', badge: 'FinTech & Trading' },
            { id: 'fitness', title: '🏋️ Gym, Fitness & Web Platforms', badge: 'Web & Mobile' },
            { id: 'tools', title: '🛠️ Shared Templates, Tooling & Infrastructure', badge: 'Infrastructure' }
        ];

        sections.forEach(sec => {
            const sectionHeading = document.createElement('div');
            sectionHeading.className = 'section-heading';
            sectionHeading.setAttribute('data-section', sec.id);
            sectionHeading.innerHTML = `\${sec.title} <span class="section-badge">\${sec.badge}</span>`;
            
            const grid = document.createElement('div');
            grid.className = 'repo-grid';
            grid.setAttribute('data-section-grid', sec.id);

            const filteredInSec = repos.filter(r => categorizeRepo(r).includes(sec.id));

            filteredInSec.forEach(repo => {
                const card = createRepoCard(repo);
                grid.appendChild(card);
            });

            if (filteredInSec.length > 0) {
                container.appendChild(sectionHeading);
                container.appendChild(grid);
            }
        });
    }

    function createRepoCard(repo) {
        const card = document.createElement('div');
        card.className = 'repo-card';
        card.setAttribute('data-name', repo.name.toLowerCase());
        card.setAttribute('data-fullname', repo.nameWithOwner.toLowerCase());
        card.setAttribute('data-category', categorizeRepo(repo));
        card.setAttribute('id', `card-\${repo.nameWithOwner.replace('/', '-')}`);

        const openStoriesUrl = `https://github.com/\${repo.nameWithOwner}/issues?q=is%3Aissue+is%3Aopen+label%3Atype%3Auser-story`;
        const prsUrl = `https://github.com/\${repo.nameWithOwner}/pulls`;
        const actionsUrl = `https://github.com/\${repo.nameWithOwner}/actions`;

        card.innerHTML = `
            <div>
                <div class="repo-header">
                    <div class="repo-title-area">
                        <a href="\${repo.url}" target="_blank" class="repo-name-link">\${repo.name}</a>
                        <span class="repo-full-name">\${repo.nameWithOwner}</span>
                    </div>
                    <span class="visibility-tag">\${repo.isPrivate ? 'Private' : 'Public'}</span>
                </div>
                <div class="repo-desc">\${repo.description || 'No description provided.'}</div>
                
                <div class="live-status-row" id="status-row-\${repo.nameWithOwner.replace('/', '-')}">
                    <span class="status-chip" id="chip-stories-\${repo.nameWithOwner.replace('/', '-')}">📌 Stories: <em>--</em></span>
                    <span class="status-chip" id="chip-prs-\${repo.nameWithOwner.replace('/', '-')}">🔀 PRs: <em>--</em></span>
                    <span class="status-chip" id="chip-ci-\${repo.nameWithOwner.replace('/', '-')}">⚡ CI: <em>--</em></span>
                </div>
            </div>

            <div class="link-group">
                <a href="\${openStoriesUrl}" target="_blank" class="btn-link btn-story">
                    📌 Open User Stories (is:open type:user-story)
                </a>
                <a href="\${prsUrl}" target="_blank" class="btn-link btn-pr">
                    🔀 Pull Requests
                </a>
                <a href="\${actionsUrl}" target="_blank" class="btn-link btn-actions">
                    ⚡ GitHub Actions
                </a>
            </div>
        `;
        return card;
    }

    function filterRepos() {
        const query = document.getElementById('searchInput').value.toLowerCase();
        const cards = document.querySelectorAll('.repo-card');

        cards.forEach(card => {
            const name = card.getAttribute('data-name') || '';
            const fullname = card.getAttribute('data-fullname') || '';
            const desc = card.querySelector('.repo-desc')?.textContent.toLowerCase() || '';
            
            if (name.includes(query) || fullname.includes(query) || desc.includes(query)) {
                card.style.display = 'flex';
            } else {
                card.style.display = 'none';
            }
        });

        updateSectionVisibility();
    }

    function setCategoryFilter(category, btn) {
        document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        const cards = document.querySelectorAll('.repo-card');
        cards.forEach(card => {
            const cardCat = card.getAttribute('data-category') || '';
            if (category === 'all' || cardCat.includes(category)) {
                card.style.display = 'flex';
            } else {
                card.style.display = 'none';
            }
        });

        updateSectionVisibility();
    }

    function updateSectionVisibility() {
        document.querySelectorAll('.section-heading').forEach(heading => {
            const sec = heading.getAttribute('data-section');
            const grid = document.querySelector(`[data-section-grid="\${sec}"]`);
            if (grid) {
                const visibleCards = Array.from(grid.querySelectorAll('.repo-card')).some(c => c.style.display !== 'none');
                heading.style.display = visibleCards ? 'flex' : 'none';
                grid.style.display = visibleCards ? 'grid' : 'none';
            }
        });
    }

    function refreshLocalView() {
        document.getElementById('searchInput').value = '';
        document.querySelectorAll('.filter-btn').forEach((b, i) => b.classList.toggle('active', i === 0));
        renderRepositories(repositoriesData);
        showStatus('Directory view refreshed from snapshot data.', 'info');
    }

    async function refreshStatusFromGitHub() {
        const btn = document.getElementById('btnLiveStatus');
        btn.disabled = true;
        btn.innerHTML = '⏳ Querying GitHub API...';
        showStatus('Querying live open stories, PR counts, and CI status from GitHub.com...', 'info');

        const token = localStorage.getItem('gh_pat_token') || '';
        const headers = { 'Accept': 'application/vnd.github.v3+json' };
        if (token) {
            headers['Authorization'] = `token \${token}`;
        }

        let completed = 0;
        const total = repositoriesData.length;

        for (const repo of repositoriesData) {
            const safeKey = repo.nameWithOwner.replace('/', '-');
            const chipStories = document.getElementById(`chip-stories-\${safeKey}`);
            const chipPrs = document.getElementById(`chip-prs-\${safeKey}`);
            const chipCi = document.getElementById(`chip-ci-\${safeKey}`);

            try {
                // 1. Fetch Open User Stories count
                const storiesRes = await fetch(`https://api.github.com/search/issues?q=repo:\${repo.nameWithOwner}+type:issue+state:open+label:type:user-story`, { headers });
                if (storiesRes.ok) {
                    const storiesData = await storiesRes.json();
                    const count = storiesData.total_count || 0;
                    if (chipStories) {
                        chipStories.innerHTML = `📌 Stories: <strong>\${count}</strong>`;
                        chipStories.className = count > 0 ? 'status-chip purple' : 'status-chip';
                    }
                }

                // 2. Fetch Open PRs count
                const prsRes = await fetch(`https://api.github.com/repos/\${repo.nameWithOwner}/pulls?state=open&per_page=1`, { headers });
                if (prsRes.ok) {
                    // Use link header or count
                    const prsData = await prsRes.json();
                    const prCount = prsData.length;
                    if (chipPrs) {
                        chipPrs.innerHTML = `🔀 PRs: <strong>\${prCount >= 1 ? 'Open' : '0'}</strong>`;
                        chipPrs.className = prCount > 0 ? 'status-chip green' : 'status-chip';
                    }
                }

                // 3. Fetch latest Action Workflow status
                const runsRes = await fetch(`https://api.github.com/repos/\${repo.nameWithOwner}/actions/runs?per_page=1`, { headers });
                if (runsRes.ok) {
                    const runsData = await runsRes.json();
                    if (runsData.workflow_runs && runsData.workflow_runs.length > 0) {
                        const lastRun = runsData.workflow_runs[0];
                        const conclusion = lastRun.conclusion || lastRun.status;
                        if (chipCi) {
                            if (conclusion === 'success') {
                                chipCi.innerHTML = `⚡ CI: 🟢 Pass`;
                                chipCi.className = 'status-chip green';
                            } else if (conclusion === 'failure') {
                                chipCi.innerHTML = `⚡ CI: 🔴 Fail`;
                                chipCi.className = 'status-chip red';
                            } else if (conclusion === 'in_progress') {
                                chipCi.innerHTML = `⚡ CI: 🔵 Running`;
                                chipCi.className = 'status-chip orange';
                            } else {
                                chipCi.innerHTML = `⚡ CI: \${conclusion}`;
                                chipCi.className = 'status-chip';
                            }
                        }
                    } else {
                        if (chipCi) chipCi.innerHTML = `⚡ CI: None`;
                    }
                }
            } catch (err) {
                console.warn(`Error querying \${repo.nameWithOwner}:`, err);
            }

            completed++;
            showStatus(`Live GitHub Query in progress: \${completed}/\${total} repositories audited...`, 'info');
        }

        btn.disabled = false;
        btn.innerHTML = '⚡ Refresh Status in GitHub.com';
        showStatus('Live GitHub status update complete for all repositories!', 'success');
    }
</script>

</body>
</html>
"@

# Write to Output Path
$outputDir = Split-Path -Path $OutputPath -Parent
if (-not (Test-Path -LiteralPath $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
}
Set-Content -LiteralPath $OutputPath -Value $html -Encoding utf8
Write-Host "Successfully generated HTML dashboard at: $OutputPath" -ForegroundColor Green
