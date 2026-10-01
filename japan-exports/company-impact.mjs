const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function renderCompanyImpact(theme,growth){
 const n=theme?.impact;if(!n)return '';
 const direction=!Number.isFinite(growth)?'판단 보류':growth>0?'수출 증가':growth<0?'수출 감소':'수출 보합';
 const current=!Number.isFinite(growth)?'비교 가능한 3개월 수치 부족':growth>0?n.up:growth<0?n.down:'수출 금액만으로 뚜렷한 방향 판단 어려움';
 const exposures=(n.exposures||[]).map(e=>`<span><b>${esc(e.company)} ${esc(e.business)} ${esc((100*e.numerator/e.denominator).toFixed(1))}%</b> · ${esc(e.period)}</span>`).join('<br>')||esc(n.exposureNote);
 const detail=(n.exposures||[]).map(e=>`<p>${esc(e.company)}: ${esc(e.numerator.toLocaleString('ko-KR'))} ÷ ${esc(e.denominator.toLocaleString('ko-KR'))}백만 엔 × 100 · 공식 매출로 계산. ${esc(e.note)}</p>`).join('');
 const links=[...new Map(n.sources.map(s=>[s.url,s])).values()].filter(s=>/^https:\/\//.test(s.url));
 return `<div class="company-impact"><p class="impact-current"><b>${esc(direction)}</b> · ${esc(current)}</p><dl class="impact-shares"><div><dt>일본 수출 내 비중</dt><dd>산출 불가 · 최대 수출기업 미확인</dd></div><div><dt>회사 매출 내 관련 사업</dt><dd>${exposures}</dd></div></dl><details class="impact-detail"><summary>좋은 경우 / 나쁜 경우 · 비중 근거</summary><p><b>증가가 수요 확대라면</b> ${esc(n.up)}</p><p><b>감소가 수요 둔화라면</b> ${esc(n.down)}</p><p>${esc(n.caution)}</p><p>기업명은 점유율 순위가 아닙니다. 사용 중인 품목·국가별 통계에는 수출기업 구분이 없어 최대 기여기업과 점유율을 확정하지 않습니다. 위 사업 비중은 전 세계·전 제품 매출 기준이며 선택 국가의 수출 비중이 아닙니다. 증가·감소 해석은 조건부 분석으로 실적·주가 예측이 아닙니다.</p>${detail}<div class="impact-links">${links.map(s=>`<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.label)} ↗</a>`).join('')}</div><small>기업 연결 검토 ${esc(n.reviewedAt)} · 수출 지표는 선택 국가 기준</small></details></div>`;
}
