# 일본 월매출 실패 119개 조사 · 2026-10-13

119개 전수 확인: 매출·보험료 수입 수치 복구 97개, 매출 외 지표 18개, 공시 중단 1개, 현재 수치 미확보 3개.

검증 데이터에 새 월별 값 432개를 추가했다. 회사·브랜드·사업부·전점·기존점 범위를 구분한다. 기본 시총 1조 원 필터와 최근 24개월 표시는 유지한다.

복구는 확보된 공개 월에 한한다. 모든 회사의 최신 24개월이 완성됐다는 뜻은 아니다. 안라쿠테이는 연간 자료의 월별 수치, 유토리는 분기 설명자료의 월별 수치를 사용한다. 이미지·특수 PDF·분기 설명자료의 수동 검증 경로는 모든 회사가 완전 자동화된 것은 아니다.

## 현재 월별 수치를 확보하지 못한 곳

- 1379 호쿠토: 확인된 월별 자료는 2023년 3월까지. 공식 공시 중단 확인은 안 됨.
- 3069 JFLA: 확인된 월별 자료는 2023년 3월까지.
- 8798 어드밴스크리에이트: 매출 차트에 정확한 수치가 없으며 ANP는 매출액과 다름.
- 3927 후바브레인: 2023년 3월분 이후 월차 매출 공시 중단 공지 확인.

## 재발 방지

날짜 형식·반기 표·공식 JSON/JavaScript 공시 목록·확장자 없는 PDF를 읽도록 보완했다. 회사별 순환 수집, 실패 PDF 재시도, 같은 주소로 갱신되는 PDF 재확인도 추가했다. 조사 출처와 지표는 `data/monthly_recovery_jp.json`, 반복 수집 경로는 `data/monthly_sources_jp.json`에 보관한다.

## 원문에 따른 정정

- 라운드원 2026-08: 121.7 → 121.8
- 코메리 2026-07: 101.6 → 101.5
- 월드 2026-08: 108.9 → 109.0 (회사 정정 공지)

GMO파이낸셜은 연결 영업수익의 공시 금액으로 전년비를 계산했다. EPCO는 자회사 ENE’s 매출만 표시하며 연결 전체와 구분한다. 10월 13일 발표된 이온·이온홋카이도 9월 자료도 포함했다.

## 회사별 결과

| 코드 | 회사 | 결과 | 확보한 최신 월 | 출처 |
|---|---|---|---|---|
| 1379 | ホクト | 현재 수치 미확보 | 2023-03 | [원문](https://www.hokto-kinoko.co.jp/corporate/ir/gyouseki/sokuhou/) |
| 1417 | ミライト・ワン | 매출 외 지표 | — | [원문](https://www.mirait-one.com/info/001973.html) |
| 1911 | 住友林業 | 매출 외 지표 | — | [원문](https://sfc.jp/information/ir/zaimu/order/) |
| 205A | ロゴスＨＤ | 매출 외 지표 | — | [원문](https://logos-holdings.jp/ir/order/) |
| 2154 | オープンアップグループ | 매출 외 지표 | — | [원문](https://openupgroup.co.jp/ir/result-employees/) |
| 2294 | 柿安本店 | 수치 복구 | — | [원문](https://www.kakiyasuhonten.co.jp/ir/financial/month.php) |
| 2311 | エプコ | 수치 복구 | 2026-08 | [원문](https://www.epco.co.jp/ir) |
| 2337 | いちご | 매출 외 지표 | — | [원문](https://www.ichigo.gr.jp/ir/news/) |
| 2461 | ファンコミュニケーションズ | 수치 복구 | — | [원문](https://www.fancs.com/news/376546549/) |
| 2502 | アサヒグループ | 수치 복구 | — | [원문](https://www.asahigroup-holdings.com/en/ir/financial_data/monthly_data/) |
| 2590 | ＤｙＤｏ | 매출 외 지표 | — | [원문](https://holdings.dydo.co.jp/ir/data/monthly_report/) |
| 2593 | 伊藤園 | 수치 복구 | — | [원문](https://www.itoen.co.jp/ir/library/monthly_sales_backnumber/) |
| 262A | インターメスティック | 수치 복구 | — | [원문](https://www.zoff.com/ir/monthly/) |
| 2654 | アスモ | 수치 복구 | — | [원문](https://www.asmo1.co.jp) |
| 2669 | カネ美食品 | 수치 복구 | 2026-09 | [원문](https://www.kanemi-foods.co.jp/ir/monthly.html) |
| 2674 | ハードオフコーポレーション | 수치 복구 | — | [원문](https://www.hardoff.co.jp/ir/library/sales/) |
| 2678 | アスクル | 수치 복구 | — | [원문](https://www.askul.co.jp/corp/investor/finance/) |
| 2685 | アンドエスティＨＤ | 수치 복구 | — | [원문](https://www.andst-hd.co.jp/ir/highlights/sales/fy2027/) |
| 2695 | くら寿司 | 수치 복구 | 2026-09 | [원문](https://www.kurasushi.co.jp/company/ir/library/monthly_reports.html) |
| 2698 | キャンドゥ | 수치 복구 | — | [원문](https://www.cando-web.co.jp/ir/monthly_report/) |
| 2705 | 大戸屋ホールディングス | 수치 복구 | — | [원문](https://www.ootoya.jp/ir/financial/monthly.html) |
| 2722 | ＩＫホールディングス | 수치 복구 | 2026-09 | [원문](https://www.ai-kei.co.jp/ir/library_monthly) |
| 2730 | エディオン | 수치 복구 | — | [원문](https://www.edion.co.jp/ir/library/monthly) |
| 2742 | ハローズ | 수치 복구 | — | [원문](https://www.halows.com/ir/finance_monthly/) |
| 274A | ガーデン | 수치 복구 | — | [원문](https://ir-gardengroup.jp/) |
| 2753 | あみやき | 수치 복구 | — | [원문](https://amiyakitei.co.jp/ir/ir_monthly/) |
| 2792 | ハニーズＨＬＤ | 수치 복구 | 2026-09 | [원문](https://www.honeys.co.jp/ir/data) |
| 2910 | ロックフィルド | 수치 복구 | — | [원문](https://www.rockfield.co.jp/ir/library/monthly/) |
| 3030 | ハブ | 수치 복구 | — | [원문](https://www.pub-hub.co.jp/ir/month.html) |
| 3038 | 神戸物産 | 수치 복구 | — | [원문](https://www.kobebussan.co.jp/ir/monthly.php) |
| 3058 | 三洋堂ホールディングス | 수치 복구 | — | [원문](https://ir.sanyodo.co.jp/ir/irnews.html) |
| 3068 | ＷＤＩ | 수치 복구 | — | [원문](https://www.wdi.co.jp/ir/monthly.html) |
| 3069 | ＪＦＬＡホールディングス | 현재 수치 미확보 | 2023-03 | [원문](https://j-fla.com/ir/monthly/) |
| 3077 | ホリイフード | 수치 복구 | — | [원문](https://www.horiifood.co.jp/ir/library/prospectus.cgi?id=18) |
| 3080 | ジェーソン | 수치 복구 | — | [원문](https://jason.co.jp/ir/monthly_information_2027_2) |
| 3087 | ドトール・日レスＨＤ | 수치 복구 | — | [원문](https://www.doutor.co.jp/ir/library/monthly/) |
| 3093 | トレファク | 수치 복구 | — | [원문](https://www.treasurefactory.co.jp/ir/monthly.html) |
| 3097 | 物語コーポ | 수치 복구 | — | [원문](https://www.monogatari.co.jp/ir/date/) |
| 3099 | 三越伊勢丹ＨＤ | 수치 복구 | — | [원문](https://www.imhds.co.jp/corporate/ir/finance/monthly-report.html) |
| 3160 | 大光 | 수치 복구 | — | [원문](https://www.oomitsu.com) |
| 3195 | ジェネレーションパス | 수치 복구 | — | [원문](https://www.genepa.com/ir/) |
| 3196 | ホットランドホールディングス | 수치 복구 | 2026-09 | [원문](https://ir.hotland.co.jp/policy/monthlyinfo/) |
| 3199 | 綿半ホールディングス | 수치 복구 | — | [원문](https://watahan.co.jp/ir/trends.html) |
| 3222 | ＵＳＭＨ | 수치 복구 | 2026-08 | [원문](https://www.usmh.co.jp/ir/monthly_reports) |
| 3317 | フライングガーデン | 수치 복구 | — | [원문](https://www.fgarden.co.jp/ir/index.php) |
| 3358 | Ｔｒａｉｌｈｅａｄ Ｇｌｏｂａｌ Ｈｏｌｄｉｎｇｓ | 수치 복구 | — | [원문](https://thg-hd.com) |
| 3370 | フジタコーポレーション | 수치 복구 | — | [원문](https://www.fujitacorp.co.jp/main/irinfotop/irp07/) |
| 3395 | サンマルクホールディングス | 수치 복구 | 2026-09 | [원문](https://www.saint-marc-hd.com/hd/ir/) |
| 3543 | コメダＨＤ | 수치 복구 | — | [원문](https://komeda-holdings.co.jp/monthly/) |
| 3544 | サツドラＨＤ | 수치 복구 | — | [원문](https://satudora-hd.co.jp/ir/monthly/) |
| 3548 | バロック | 수치 복구 | — | [원문](https://www.baroque-global.com/jp/ir/monthly/) |
| 3549 | クスリのアオキＨＤ | 수치 복구 | — | [원문](https://kusuri-aoki-hd.co.jp/ir/finance/monthry/) |
| 3591 | ワコールＨＤ | 수치 복구 | — | [원문](https://www.wacoalholdings.jp/ir/monthlydata/) |
| 3608 | ＴＳＩ ＨＤ | 수치 복구 | — | [원문](https://www.tsi-holdings.com/tsuki.html) |
| 3612 | ワールド | 수치 복구 | 2026-09 | [원문](https://corp.world.co.jp/ir/library/monthly/) |
| 3663 | セルシス | 매출 외 지표 | — | [원문](https://www.celsys.com/irinfo_news/contents_type=47) |
| 3923 | ラクス | 수치 복구 | — | [원문](https://www.rakus.co.jp/ir/monthly-sales/) |
| 3927 | フーバーブレイン | 공시 중단 | — | [원문](https://www.fuva-brain.co.jp/ir/) |
| 4666 | パーク２４ | 수치 복구 | — | [원문](https://www.park24.co.jp/ir/financial/monthly.html) |
| 4679 | 田谷 | 수치 복구 | 2026-09 | [원문](https://www.taya.co.jp/ir/library/monthly.html) |
| 4680 | ラウンドワン | 수치 복구 | — | [원문](https://www.round1-group.co.jp/en/ir/financial-affairs/sales/) |
| 5580 | プロディライト | 매출 외 지표 | — | [원문](https://prodelight.co.jp/ir/) |
| 5892 | ｙｕｔｏｒｉ | 수치 복구 | 2026-06 | [원문](https://yutori.tokyo/ir/presentations/) |
| 6036 | ＫｅｅＰｅｒ技研 | 수치 복구 | — | [원문](https://keepergiken.co.jp/ir/irdata/monthly) |
| 6045 | レントラックス | 수치 복구 | — | [원문](https://www.rentracks.co.jp/ir/finance/others.html) |
| 6073 | アサンテ | 수치 복구 | 2026-09 | [원문](https://www.asante.co.jp/ir/library/disclosure/) |
| 7157 | ライフネット生命保険 | 매출 외 지표 | — | [원문](https://ir.lifenet-seimei.co.jp/ja/ir/financial/monthly.html) |
| 7177 | ＧＭＯフィナンシャルホールディングス | 수치 복구 | 2026-08 | [원문](https://www.gmofh.com/ir/news.html) |
| 7412 | アトム | 수치 복구 | — | [원문](https://www.atom-corp.co.jp/ir/monthlysale.php) |
| 7512 | イオン北海 | 수치 복구 | — | [원문](https://www.aeon-hokkaido.jp/corporation/month/) |
| 7513 | コジマ | 수치 복구 | 2026-09 | [원문](https://www.kojima.net/corporation/ir/bulletin.html) |
| 7522 | ワタミ | 수치 복구 | — | [원문](https://www.watami.co.jp/ir/library/recent/) |
| 7545 | 西松屋チェーン | 수치 복구 | — | [원문](https://www.24028.jp/ir/library) |
| 7550 | ゼンショー | 수치 복구 | — | [원문](https://www.zensho.co.jp/jp/ir/finance/monthly/sukiya/) |
| 7562 | 安楽亭 | 수치 복구 | — | [원문](https://www.anrakutei.co.jp/ir.html) |
| 7585 | かんなん丸 | 수치 복구 | — | [원문](https://kannanmaru.co.jp/) |
| 7596 | 魚力 | 수치 복구 | — | [원문](https://uoriki.co.jp/ir/library/monthly.html) |
| 7604 | 梅の花グループ | 수치 복구 | — | [원문](https://www.umenohana.co.jp/investor/archive?category=monthly_sales) |
| 7610 | テイツー | 수치 복구 | 2026-08 | [원문](https://www.tay2.co.jp/ir/irlibrary/01.html) |
| 7630 | 壱番屋 | 수치 복구 | — | [원문](https://www.ichibanya.jp/ir/finance/monthly/) |
| 7646 | ＰＬＡＮＴ | 수치 복구 | — | [원문](https://www.plant-co.jp/ir/monthly.html) |
| 7674 | ＮＡＴＴＹ ＳＷＡＮＫＹホールディングス | 수치 복구 | — | [원문](https://nattyswanky.com/ir/) |
| 7682 | 浜木綿 | 수치 복구 | — | [원문](https://ir.hamayuu.co.jp/ja/ir.html) |
| 7686 | ひとまいる | 수치 복구 | — | [원문](https://www.hitomile.co.jp/ir/monthly_report.html) |
| 7865 | ピープル | 수치 복구 | 2026-08 | [원문](https://www.people-kk.co.jp/investorrelations/monthly/) |
| 8142 | トーホー | 수치 복구 | 2026-08 | [원문](https://www.to-ho.co.jp/ir/flash.html) |
| 8160 | 木曽路 | 수치 복구 | — | [원문](https://www.kisoji.co.jp/ir/monthly.html) |
| 8194 | ライフコーポ | 수치 복구 | — | [원문](https://www.lifecorp.jp/company/ir/financial/monthly/) |
| 8198 | ＭＶ東海 | 수치 복구 | — | [원문](https://www.mv-tokai.co.jp/ir-month-tokai/) |
| 8207 | テンアライド | 수치 복구 | — | [원문](https://www.teng.co.jp/ir/ir.html) |
| 8214 | ＡＯＫＩホールディングス | 수치 복구 | 2026-09 | [원문](https://ir.aoki-hd.co.jp/ja/ir/finance/monthlyreport.html) |
| 8218 | コメリ | 수치 복구 | — | [원문](https://www.komeri.bit.or.jp/ir/monthlytrend/) |
| 8244 | 近鉄百貨店 | 수치 복구 | — | [원문](https://www.d-kintetsu.co.jp/corporate/ir/finance/recent_results.html) |
| 8267 | イオン | 수치 복구 | 2026-09 | [원문](https://www.aeon.info/ir/library/monthly/) |
| 8279 | ヤオコー | 수치 복구 | — | [원문](https://www.bluezonesholdings.co.jp/ir/finance/monthly.html) |
| 8410 | セブン銀行 | 매출 외 지표 | 2026-09 | [원문](https://www.sevenbank.co.jp/ir/finance/monthly_trend/) |
| 8628 | 松井証券 | 수치 복구 | — | [원문](https://www.matsui.co.jp/company/ir/disclosure/business/) |
| 8715 | アニコムホールディングス | 수치 복구 | — | [원문](https://www.anicom.co.jp/ir/financial/business/) |
| 8742 | 小林洋行 | 매출 외 지표 | — | [원문](https://www.kobayashiyoko.com) |
| 8798 | アドバンスクリエイト | 현재 수치 미확보 | — | [원문](https://www.advancecreate.co.jp/ir/topics/monthly_report/) |
| 8914 | エリアリンク | 매출 외 지표 | — | [원문](https://www.arealink.co.jp/news/ir/p17078/) |
| 8963 | Ｒ－ＩＮＶ | 수치 복구 | — | [원문](https://www.invincible-inv.co.jp) |
| 8975 | Ｒ－いちごオフィス | 매출 외 지표 | — | [원문](https://www.ichigo-office.co.jp/portfolio/ratio.html) |
| 8985 | Ｒ－ＪＨＲ | 수치 복구 | — | [원문](https://www.jhrth.co.jp/ja/portfolio/review.html) |
| 9039 | サカイ引越センター | 수치 복구 | — | [원문](https://www.hikkoshi-sakai.co.jp) |
| 9163 | ナレルグループ | 매출 외 지표 | 2026-08 | [원문](https://nareru-group.co.jp/ir/finance/indicator/) |
| 9166 | ＧＥＮＤＡ | 수치 복구 | — | [원문](https://genda.jp/2026/09/16/monthly-report-2609/) |
| 9206 | スターフライヤー | 매출 외 지표 | 2026-08 | [원문](https://www.starflyer.jp/starflyer/ir/traffic_figures/) |
| 9279 | ギフトホールディングス | 수치 복구 | — | [원문](https://www.gift-group.co.jp/ir/library/04) |
| 9282 | Ｉ－いちごグリーン | 매출 외 지표 | 2026-09 | [원문](https://www.ichigo-green.co.jp/) |
| 9285 | Ｉ－東京インフラＦ | 매출 외 지표 | — | [원문](https://www.tokyo-infra.com) |
| 9603 | エイチ・アイ・エス | 매출 외 지표 | 2026-08 | [원문](https://www.his.co.jp/ir/monthly-report/) |
| 9616 | 共立メンテナンス | 수치 복구 | — | [원문](https://www.kyoritsugroup.co.jp/ir/library/?category=16) |
| 9627 | アインホールディングス | 수치 복구 | — | [원문](https://www.ainj.co.jp/corporate/ir/) |
| 9831 | ヤマダ | 수치 복구 | — | [원문](https://www.yamada-holdings.jp/ir/monthly.html) |
| 9835 | ジュンテンド | 수치 복구 | — | [원문](https://www.juntendo.co.jp/ir/ir-library/monthly/) |
| 9936 | 王将フードサービス | 수치 복구 | — | [원문](https://ir.ohsho.co.jp/ir/library/monthly.html) |
| 9974 | ベルク | 수치 복구 | 2026-09 | [원문](https://www.belc.jp/company/library/monthly) |
| 9993 | ヤマザワ | 수치 복구 | — | [원문](https://yamazawa.co.jp/ir/term-month.html) |
