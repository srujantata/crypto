# daily_researcher.ps1
# Triggered by Windows Task Scheduler at 9:17 AM daily.
# Opens Claude Code in the crypto repo and runs the daily market research prompt.
# Setup: schtasks /create /tn "CryptoResearcher" /tr "powershell -File D:\Srujan\Claude\crypto\daily_researcher.ps1" /sc daily /st 09:17 /f

$repo    = "D:\Srujan\Claude\crypto"
$logfile = "$repo\researcher_log.txt"
$prompt  = @'
You are the DAILY MARKET RESEARCHER for an automated trading bot. Codebase: D:\Srujan\Claude\crypto. Railway URL: https://crypto-production-5b12.up.railway.app. Bearer token: REDACTED_SECRET

Execute these steps in order:

STEP 1 - Health check
Run PowerShell health check against the Railway /health endpoint. Confirm connected=true and note positions.

STEP 2 - Web research (use WebSearch tool for each)
1. "top trending cryptocurrency high volume breakout today 2026"
2. "best momentum stocks ADX trending breakout today NYSE NASDAQ"
3. "crypto fear greed index today VIX market sentiment"
4. "FOMC meeting date CPI report date next 14 days 2026"
5. "low volume dead cryptocurrency avoid 2026"

STEP 3 - Evaluate candidates
For any promising new symbol found, write D:\Srujan\Claude\crypto\__research_scan.py and run it. Use fetch_ohlcv + generate_signals from the project. Check: ADX capability, avg volume > $10M/day, Alpaca paper trading support (crypto: alpaca crypto list; stocks: NYSE/NASDAQ only, market cap > $5B).

STEP 4 - Read and review config.py
Check D:\Srujan\Claude\crypto\config.py for parameter fitness. Review: SYMBOL_ADX_MIN per asset, SYMBOL_ATR_MULT, HARD_STOP_PCT, VOL_SURGE_MULT, ADX_FADE_EXIT, REENTRY_COOLDOWN_SECS.

STEP 5 - Make changes only if justified
Only change config.py if there is a CONCRETE data-backed reason. Run syntax check after any edit. Commit and push to GitHub if changes made (git add config.py && git commit -m "research: ..." && git push origin main). Railway auto-deploys.

STEP 6 - Output structured daily report:
DAILY RESEARCH REPORT - [date]
SENTIMENT: [bullish/neutral/bearish] | VIX: [X] | Fear/Greed: [X/100]
MACRO RISK (next 14 days): [events or none]
SYMBOLS: Added=[list] Removed=[list] Evaluated=[list]
PARAMS: [changes or "no changes"]
NOTES: [3 key market observations]
'@

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm"
Add-Content -Path $logfile -Value "`n[$timestamp] Daily researcher starting..."

# Write prompt to temp file for claude to read
$promptFile = "$repo\__researcher_prompt.txt"
$prompt | Out-File -FilePath $promptFile -Encoding utf8

# Run claude code with the prompt piped in
# Requires 'claude' to be in PATH (Claude Code CLI)
try {
    $result = Get-Content $promptFile -Raw | claude --print 2>&1
    Add-Content -Path $logfile -Value $result
    Add-Content -Path $logfile -Value "[$timestamp] Researcher completed OK"
} catch {
    Add-Content -Path $logfile -Value "[$timestamp] ERROR: $_"
}

Remove-Item $promptFile -ErrorAction SilentlyContinue
