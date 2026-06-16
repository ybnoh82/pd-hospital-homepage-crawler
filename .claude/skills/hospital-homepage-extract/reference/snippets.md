# 재사용 evaluate 스니펫

매 실행마다 다시 짜게 되는 `browser_evaluate`/캡처 패턴 모음이다. **고정 파이프라인이 아니라 복붙용 출발점** — 사이트 구조에 맞게 셀렉터·정규식을 조정해 쓴다. (이 스킬은 정해진 파싱 스크립트가 없다; 여기 스니펫은 선택적 헬퍼다.)

---

## 1. 정찰 덤프 (§0의 2단계)
홈에서 메뉴·푸터·본문·이미지 목록을 한 번에. 사이트 유형 판단과 병원 확인의 공통 입력.

```js
() => {
  const links = [...document.querySelectorAll('header a, nav a, .gnb a, #gnb a, .menu a, a')]
    .map(a => ({ t: a.innerText.trim().replace(/\s+/g, ' '), h: a.href }))
    .filter(l => l.t && l.h && !l.h.includes('javascript'));
  const navLinks = [...new Map(links.map(l => [l.t + l.h, l])).values()].slice(0, 80);
  const body = document.body.innerText.replace(/\n{2,}/g, '\n').trim();
  const fIdx = body.search(/사업자|대표자|대표원장|상호|TEL|대표전화/);
  const imgs = [...document.querySelectorAll('img')]
    .map(i => ({ f: decodeURIComponent((i.currentSrc || i.src).split('/').pop()), alt: i.alt, w: i.naturalWidth }))
    .filter(o => o.w > 150 && !/logo|icon|btn|blank|\.gif/i.test(o.f));
  return {
    title: document.title,
    navLinks,
    footer: body.slice(Math.max(0, fIdx - 60)).slice(0, 800),
    bodySample: body.slice(0, 1500),
    imgs: imgs.slice(0, 40),
  };
}
```

판단: `navLinks`/`bodySample`에 제품·장비명이 텍스트로 보이면 **텍스트형**, 섹션 제목만 있고 `imgs`만 많으면 **이미지형**(§0의 4단계). `footer`로 병원 확인(§0의 3단계).

---

## 2. 페이지 본문 텍스트 추출 (텍스트형 페이지)
헤더·푸터(메뉴·지점목록·사업자정보)를 걷어내고 본문 시술명·가격만. 셀렉터/마커는 사이트마다 다르니 조정한다.

```js
async () => {
  for (let y = 0; y < document.body.scrollHeight; y += 800) { window.scrollTo(0, y); await new Promise(r => setTimeout(r, 70)); }
  window.scrollTo(0, 0);
  const main = document.querySelector('#container,#contents,.contents,main') || document.body;
  let text = main.innerText.replace(/\n{2,}/g, '\n').trim();
  // 흔한 잡음 제거(사이트에 맞게): 상단 글로벌 메뉴 ~ 푸터 지점목록
  text = text.split(/강남본점|상호명:|COPYRIGHT/i)[0];
  const imgs = [...main.querySelectorAll('img')]
    .map(i => ({ f: decodeURIComponent((i.currentSrc || i.src).split('/').pop()), alt: i.alt, w: i.naturalWidth }))
    .filter(o => o.w > 200);
  // CSS 배경 이미지도 같이(숨은 콘텐츠 대비, §Gotchas)
  const bg = new Set();
  main.querySelectorAll('*').forEach(el => {
    const m = getComputedStyle(el).backgroundImage.match(/url\(["']?(.*?)["']?\)/);
    if (m && /\.(png|jpe?g|webp)/i.test(m[1])) bg.add(decodeURIComponent(m[1].split('/').pop()));
  });
  return { text: text.slice(0, 4000), imgs: imgs.slice(0, 30), bg: [...bg].slice(0, 20) };
}
```

---

## 3. 초장축 이미지 분할 캡처 (§4 효율 기법)
아주 긴 세로 이미지(가격표·의료진·시술 설명)는 통째로 읽으면 다운스케일로 글자가 뭉개진다. 판독 가능한 고정폭으로 렌더한 뒤 구간별로 캡처해 순서대로 `Read`한다.

먼저 이미지 단독뷰로 이동(`browser_navigate`로 이미지 URL), 그다음:

```js
// (A) 개요 먼저 — 작은 폭으로 전체를 1컷, 어디에 글자/제품이 있는지 위치만 파악
async () => {
  const src = location.href;
  document.body.style.margin = '0'; document.body.style.background = '#fff';
  document.body.innerHTML = `<img src="${src}" style="width:320px;display:block">`;
  await new Promise(r => setTimeout(r, 300));
  return document.querySelector('img').naturalHeight;
}
// → 이 상태에서 browser_take_screenshot(fullPage). 글자 구간을 확인.

// (B) 확대 — 판독 가능한 고정폭(약 800px)으로 렌더하고 구간을 스크롤하며 캡처
async (y) => {                       // y: 캡처 시작 높이(px), 화면 높이만큼 겹치게 내려가며 호출
  const src = location.href;
  document.body.style.margin = '0'; document.body.style.background = '#fff';
  document.body.innerHTML = `<img src="${src}" style="width:820px;display:block">`;
  await new Promise(r => setTimeout(r, 300));
  window.scrollTo(0, y);
  await new Promise(r => setTimeout(r, 200));
  return 'ok';
}
// → 각 y 위치에서 browser_take_screenshot(viewport)로 캡처 후 Read. 짧은 이미지면 (B) 한 번 + fullPage로 끝.
```

고정 오버레이(퀵메뉴·상담폼)가 이미지를 가리면, 위처럼 이미지 단독뷰 URL로 가서 `body.innerHTML`을 교체하면 오버레이가 사라진다.
