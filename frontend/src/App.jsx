import { useCallback, useEffect, useState } from "preact/hooks";

const TOKEN_KEY = "coldchain_token";
const USER_KEY = "coldchain_user";

function verdictClass(v, status) {
  if (v === "合格") return "tag pass";
  if (v === "超温") return "tag fail";
  if (status === "pending" || status === "processing") return "tag wait";
  return "tag wait";
}

function displayVerdict(row) {
  if (row.verdict) return row.verdict;
  if (row.status === "pending") return "待处理";
  if (row.status === "processing") return "处理中";
  return "—";
}

function displayStatus(status) {
  if (status === "pending") return "待审";
  if (status === "processing") return "处理中";
  if (status === "done") return "已办结";
  return status;
}

function formatTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

export function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [user, setUser] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY) || "null");
    } catch {
      return null;
    }
  });
  const [view, setView] = useState("readings");
  const [loginForm, setLoginForm] = useState({ username: "logger", password: "log123456" });
  const [submitForm, setSubmitForm] = useState({ probe_id: "", temp_c: "" });
  const [rows, setRows] = useState([]);
  const [packages, setPackages] = useState([]);
  const [selectedPackage, setSelectedPackage] = useState(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);
  const [packing, setPacking] = useState(false);

  const authHeaders = useCallback(() => {
    const h = { "Content-Type": "application/json" };
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }, [token]);

  const loadReadings = useCallback(async () => {
    if (!token) return;
    const res = await fetch("/api/readings", { headers: authHeaders() });
    if (!res.ok) {
      setError("加载列表失败，请重新登录");
      return;
    }
    setRows(await res.json());
  }, [token, authHeaders]);

  const loadPackages = useCallback(async () => {
    if (!token) return;
    const res = await fetch("/api/departure-packages", { headers: authHeaders() });
    if (!res.ok) {
      setError("加载历史核对包失败");
      return;
    }
    setPackages(await res.json());
  }, [token, authHeaders]);

  useEffect(() => {
    if (!token) return undefined;
    loadReadings();
    const t = setInterval(loadReadings, 3000);
    return () => clearInterval(t);
  }, [loadReadings, token]);

  useEffect(() => {
    if (!token || view !== "departure") return undefined;
    loadPackages();
    const t = setInterval(loadPackages, 3000);
    return () => clearInterval(t);
  }, [loadPackages, token, view]);

  async function onLogin(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(loginForm),
      });
      if (!res.ok) {
        setError("用户名或密码错误");
        return;
      }
      const data = await res.json();
      localStorage.setItem(TOKEN_KEY, data.access_token);
      localStorage.setItem(
        USER_KEY,
        JSON.stringify({ username: data.username, role: data.role })
      );
      setToken(data.access_token);
      setUser({ username: data.username, role: data.role });
      setView("readings");
    } finally {
      setLoading(false);
    }
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
    setRows([]);
    setPackages([]);
    setSelectedPackage(null);
    setView("readings");
  }

  async function onSubmit(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    setLoading(true);
    try {
      const res = await fetch("/api/readings", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          probe_id: submitForm.probe_id,
          temp_c: parseFloat(submitForm.temp_c),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail || "提交失败");
        return;
      }
      setMsg(data.message || "已提交");
      setSubmitForm({ probe_id: "", temp_c: "" });
      await loadReadings();
    } finally {
      setLoading(false);
    }
  }

  async function openPackage(packageId) {
    setError("");
    const res = await fetch(`/api/departure-packages/${packageId}`, {
      headers: authHeaders(),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setError(data.detail || "打开核对包失败");
      setSelectedPackage(null);
      return;
    }
    setSelectedPackage(data);
  }

  async function onPack() {
    setError("");
    setMsg("");
    setPacking(true);
    try {
      const res = await fetch("/api/departure-packages", {
        method: "POST",
        headers: authHeaders(),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail || "一键打包失败");
        return;
      }
      setMsg(`已打包 ${data.item_count} 笔在途读数`);
      setSelectedPackage(data);
      await loadPackages();
    } finally {
      setPacking(false);
    }
  }

  if (!token) {
    return (
      <div class="wrap">
        <h1>冷链探头超温台</h1>
        <p class="sub">记录员提交探头编号与摄氏温度，后台工人认领后判定合格或超温。</p>
        <div class="card">
          <form onSubmit={onLogin}>
            <div class="row">
              <label>
                用户名
                <input
                  value={loginForm.username}
                  onInput={(e) =>
                    setLoginForm({ ...loginForm, username: e.target.value })
                  }
                />
              </label>
              <label>
                密码
                <input
                  type="password"
                  value={loginForm.password}
                  onInput={(e) =>
                    setLoginForm({ ...loginForm, password: e.target.value })
                  }
                />
              </label>
              <button type="submit" disabled={loading}>
                登录
              </button>
            </div>
            {error && <p class="err">{error}</p>}
          </form>
          <p class="sub" style={{ marginBottom: 0 }}>
            记录员 logger / log123456 · 观察员 watcher / watch123456
          </p>
        </div>
      </div>
    );
  }

  const isWriter = user?.role === "writer";
  const activeCount = rows.filter(
    (r) => r.status === "pending" || r.status === "processing"
  ).length;

  return (
    <div class="wrap">
      <div class="topbar">
        <div>
          <h1>冷链探头超温台</h1>
          <p class="sub">温度不超过 8℃ 为合格，否则为超温。</p>
        </div>
        <div class="top-actions">
          <nav class="tabs" aria-label="主导航">
            <button
              type="button"
              class={view === "readings" ? "active" : ""}
              onClick={() => setView("readings")}
            >
              读数台
            </button>
            <button
              type="button"
              class={view === "departure" ? "active" : ""}
              onClick={() => setView("departure")}
            >
              发车核对
            </button>
          </nav>
          <div class="user">
            {user?.username}（{isWriter ? "记录员" : "观察员"}）
            <button type="button" class="secondary" style={{ marginLeft: "0.5rem" }} onClick={logout}>
              退出
            </button>
          </div>
        </div>
      </div>

      {view === "readings" && (
        <>
          {isWriter && (
            <div class="card">
              <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>提交读数</h2>
              <form onSubmit={onSubmit}>
                <div class="row">
                  <label>
                    探头编号
                    <input
                      required
                      value={submitForm.probe_id}
                      onInput={(e) =>
                        setSubmitForm({ ...submitForm, probe_id: e.target.value })
                      }
                      placeholder="例如 探头C03"
                    />
                  </label>
                  <label>
                    温度（℃）
                    <input
                      required
                      type="number"
                      step="0.1"
                      value={submitForm.temp_c}
                      onInput={(e) =>
                        setSubmitForm({ ...submitForm, temp_c: e.target.value })
                      }
                    />
                  </label>
                  <button type="submit" disabled={loading}>
                    提交
                  </button>
                </div>
                {error && <p class="err">{error}</p>}
                {msg && <p class="ok">{msg}</p>}
              </form>
            </div>
          )}

          <div class="card">
            <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>读数列表</h2>
            <table>
              <thead>
                <tr>
                  <th>编号</th>
                  <th>探头</th>
                  <th>温度℃</th>
                  <th>结论</th>
                  <th>说明</th>
                  <th>状态</th>
                  <th>提交人</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td>{r.id}</td>
                    <td>{r.probe_id}</td>
                    <td>{r.temp_c}</td>
                    <td>
                      <span class={verdictClass(r.verdict, r.status)}>
                        {displayVerdict(r)}
                      </span>
                    </td>
                    <td>{r.reason || "—"}</td>
                    <td>{displayStatus(r.status)}</td>
                    <td>{r.created_by}</td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colspan="7">暂无数据</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}

      {view === "departure" && (
        <>
          <div class="card">
            <div class="section-head">
              <div>
                <h2 style={{ margin: 0, fontSize: "1.1rem" }}>一键打包</h2>
                <p class="sub" style={{ margin: "0.35rem 0 0" }}>
                  冻结打包瞬间全部待审、处理中读数的编号、代号与温度。
                </p>
              </div>
              {isWriter ? (
                <button type="button" onClick={onPack} disabled={packing}>
                  {packing ? "打包中…" : "一键打包"}
                </button>
              ) : (
                <span class="readonly-note">观察账号可翻包，不能打包</span>
              )}
            </div>
            {isWriter && (
              <p class="sub" style={{ marginBottom: 0 }}>
                当前读数台有 {activeCount} 笔未办结读数；打包以服务端瞬间在途集合为准。
              </p>
            )}
            {error && <p class="err">{error}</p>}
            {msg && <p class="ok">{msg}</p>}
          </div>

          <div class="card">
            <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>历史包列表</h2>
            <table>
              <thead>
                <tr>
                  <th>包号</th>
                  <th>明细笔数</th>
                  <th>打包人</th>
                  <th>打包时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {packages.map((p) => (
                  <tr key={p.id} class={selectedPackage?.id === p.id ? "selected" : ""}>
                    <td>{p.id}</td>
                    <td>{p.item_count}</td>
                    <td>{p.created_by}</td>
                    <td>{formatTime(p.created_at)}</td>
                    <td>
                      <button type="button" class="secondary small" onClick={() => openPackage(p.id)}>
                        翻包
                      </button>
                    </td>
                  </tr>
                ))}
                {packages.length === 0 && (
                  <tr>
                    <td colspan="5">暂无历史核对包</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div class="card">
            <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>包内明细</h2>
            {!selectedPackage && <p class="sub" style={{ marginBottom: 0 }}>请选择历史包查看冻结明细。</p>}
            {selectedPackage && (
              <>
                <p class="sub">
                  包号 {selectedPackage.id} · {selectedPackage.item_count} 笔 · 打包人{" "}
                  {selectedPackage.created_by} · {formatTime(selectedPackage.created_at)}
                </p>
                <table>
                  <thead>
                    <tr>
                      <th>编号</th>
                      <th>探头代号</th>
                      <th>温度℃</th>
                      <th>打包瞬间状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selectedPackage.items.map((item) => (
                      <tr key={item.reading_id}>
                        <td>{item.reading_id}</td>
                        <td>{item.probe_id}</td>
                        <td>{item.temp_c}</td>
                        <td>{displayStatus(item.reading_status)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}
