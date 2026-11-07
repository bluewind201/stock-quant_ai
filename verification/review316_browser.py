from pathlib import Path
from playwright.sync_api import sync_playwright
from sqt.paper_models import DayTradeConfig

root=Path(__file__).resolve().parents[1]
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
    page=browser.new_page()
    for source in (root/'stock-web').glob('*.js'):
        page.evaluate('(source)=>{new Function(source);}',source.read_text(encoding='utf-8'))
    page.set_content((root/'stock-web/index.html').read_text(encoding='utf-8'))
    helpers=(root/'stock-web/app.js').read_text(encoding='utf-8').split('let overview')[0]
    page.add_script_tag(content=helpers+'\nconst action=()=>{}; let api=async()=>{}; let authenticated=false; function notify(){};')
    page.add_script_tag(content=(root/'stock-web/daytrade.js').read_text(encoding='utf-8'))
    cfg=page.evaluate('''(d)=>{fillDtDefaults(d);$('dt-strategy').value='top5_morning';
        $('dt-strategy').dispatchEvent(new Event('change'));return dtConfig();}''',DayTradeConfig().model_dump())
    assert cfg['strategy']=='top5_morning'
    assert cfg['self_improve']
    validated=DayTradeConfig.model_validate(cfg)
    assert validated.entry_at=='09:05' and validated.force_close_at=='11:50'
    assert validated.basket_tp_pct==4 and validated.basket_sl_pct==2
    assert not page.locator('#dt-basket-fields').evaluate('(e)=>e.hidden')
    page.evaluate('(c)=>fillFromAccount(c)',validated.model_dump())
    assert page.locator('#dt-basket-entry').input_value()=='09:05'
    # Render actual rank, unfilled status and fee-adjusted realized PnL.
    page.evaluate('''(c)=>{dtState={id:'test',config:c,status:'stopped',phase:'closed',positions:{},
        trades:[{symbol:'000500',side:'buy',at:'2026-10-06T00:05:00Z'},
                {symbol:'000500',side:'sell',at:'2026-10-06T02:50:00Z',pnl:1234}],
        basket:{rank_source:'ai_rank',selected_at:'2026-10-05T22:30:00Z'},
        plan:{picks:[{symbol:'000500',name:'Stock',basket_rank:1,weight_pct:15,_done:true}]},
        ai:{history:[]},log:[],tuning:{},days:{},equity:10000000,cash:10000000,
        realized_pnl:1234,unrealized_pnl:0,total_return_pct:0,equity_curve:[],errors:{}};
        renderDaytrade();}''',validated.model_dump())
    assert 'AI 종목 순위' in page.locator('#dt-log').text_content()
    assert '1,234원' in page.locator('#dt-log').text_content()
    page.evaluate("""()=>{dtState.learning={current_version:'v316',proposals:[{
        status:'needs_evidence',lesson:'근거를 추가로 확인',evidence_ids:[],reason:'표본 부족'}]};
        renderDaytrade();}""")
    assert '근거 표본 부족' in page.locator('#dt-learning-out').text_content()
    assert 'v316' in page.locator('#dt-learning-out').text_content()
    page.evaluate("""async()=>{authenticated=true;api=async(path)=>path==='/financial-brief'?{
        events:[{title:'FOMC Minutes',scheduled_at:'2026-10-07T18:00:00Z',url:'https://www.federalreserve.gov'}],
        attention:[{title:'국내 행사 관심 보도'}],missing_sources:['bls_calendar'],as_of:'2026-10-07T00:00:00Z'
        }:path==='/daytrade/status'?{accounts:[],defaults:{}}:{}; await loadDaytrade();}""")
    assert 'FOMC Minutes' in page.locator('#dt-financial').text_content()
    assert '일정 미확인' in page.locator('#dt-financial').text_content()
    assert '일부 공급원 미확인' in page.locator('#dt-financial').text_content()
    browser.close()
print('TOP5, learning defaults/proposals and verified/unverified financial brief rendering: PASS')
