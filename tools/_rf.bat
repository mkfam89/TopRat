@echo off
REM In tools\ — cd to the parent, which holds scrape.py / jobpipe.py and the data.
cd /d "%~dp0.."
echo === scrape (7-day, captures real apply URLs, drops expired/old) === > logs\_rf_out.txt
python src\pipeline\scrape.py --url-key hiringcafe_7d --max-age-days 7 >> logs\_rf_out.txt 2>&1
echo === rebuild candidate list === >> logs\_rf_out.txt
python src\pipeline\jobpipe.py candidates --listings listings.json >> logs\_rf_out.txt 2>&1
echo === sample apply URLs (should be real employer/LinkedIn links) === >> logs\_rf_out.txt
python -c "import csv,io;d=open('candidates.csv',encoding='utf-8',errors='replace').read().replace(chr(0),'');r=list(csv.DictReader(io.StringIO(d)));print('rows',len(r));hc=sum(1 for x in r if 'hiringcafe.com' in (x.get('applyUrl') or ''));print('still hiringcafe.com URLs:',hc);[print(' -',(x.get('company') or '')[:22],'|',(x.get('applyUrl') or '')[:60]) for x in r[:8]]" >> logs\_rf_out.txt 2>&1
echo DONE >> logs\_rf_out.txt
