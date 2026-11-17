(() => {
  // 页面探针：只回传可见文本与地址，判定逻辑留在 Python（guard.risk_hit），
  // 这样关键词表只有一份，改规则不用动页面脚本。
  const body = document.body ? (document.body.innerText || '') : '';
  return JSON.stringify({
    url: location.href,
    title: document.title || '',
    text: body.slice(0, 6000)
  });
})()
