import { useCallback, useEffect, useState } from "preact/hooks";

function frozenStatusText(status) {
  if (status === "pending") return "待处理";
  if (status === "processing") return "处理中";
  return status || "—";
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function PackageDesk({ authHeaders, isWriter }) {
  const [packages, setPackages] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [pendingCount, setPendingCount] = useState(null);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const loadPendingCount = useCallback(async () => {
    const res = await fetch("/api/readings", { headers: authHeaders() });
    if (!res.ok) return;
    const rows = await res.json();
    setPendingCount(
      rows.filter((r) => r.status === "pending" || r.status === "processing")
        .length
    );
  }, [authHeaders]);

  const loadPackages = useCallback(async () => {
    const res = await fetch("/api/packages", { headers: authHeaders() });
    if (!res.ok) {
      setError("加载历史包列表失败");
      return [];
    }
    const list = await res.json();
    setPackages(list);
    return list;
  }, [authHeaders]);

  const loadDetail = useCallback(
    async (id) => {
      const res = await fetch(`/api/packages/${id}`, {
        headers: authHeaders(),
      });
      if (!res.ok) {
        setError("加载包内明细失败");
        return;
      }
      setDetail(await res.json());
    },
    [authHeaders]
  );

  useEffect(() => {
    (async () => {
      const list = await loadPackages();
      if (list.length > 0) {
        setSelectedId((cur) => (cur == null ? list[0].id : cur));
      }
      await loadPendingCount();
    })();
  }, [loadPackages, loadPendingCount]);

  useEffect(() => {
    if (selectedId != null) {
      loadDetail(selectedId);
    }
  }, [selectedId, loadDetail]);

  async function onPack() {
    setError("");
    setMsg("");
    setBusy(true);
    try {
      const res = await fetch("/api/packages", {
        method: "POST",
        headers: authHeaders(),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail || "打包失败");
        return;
      }
      setMsg(data.message || `已生成 #${data.id} 号发车核对包`);
      await loadPackages();
      setSelectedId(data.id);
      await loadPendingCount();
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>一键打包</h2>
        <p class="sub">
          把当前待处理与处理中的读数冻结成发车核对包，包内明细以打包瞬间的在途集合为准。
        </p>
        <div class="row" style={{ alignItems: "center" }}>
          <button type="button" onClick={onPack} disabled={!isWriter || busy}>
            一键打包
          </button>
          {pendingCount != null && (
            <span class="sub" style={{ margin: 0 }}>
              当前在途 {pendingCount} 笔
            </span>
          )}
        </div>
        {!isWriter && (
          <p class="sub" style={{ marginBottom: 0 }}>
            观察账号可翻看历史包与明细，不能执行打包。
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
              <th>打包时间</th>
              <th>操作员</th>
              <th>笔数</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {packages.map((p) => (
              <tr key={p.id} class={p.id === selectedId ? "selected" : ""}>
                <td>#{p.id}</td>
                <td>{fmtTime(p.created_at)}</td>
                <td>{p.created_by}</td>
                <td>{p.item_count}</td>
                <td>
                  <button
                    type="button"
                    class="secondary"
                    onClick={() => setSelectedId(p.id)}
                  >
                    查看明细
                  </button>
                </td>
              </tr>
            ))}
            {packages.length === 0 && (
              <tr>
                <td colspan="5">暂无历史包</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>包内明细</h2>
        {!detail && <p class="sub">请先在历史包列表中选择一份核对包。</p>}
        {detail && (
          <>
            <p class="sub">
              #{detail.id} 号包 · {fmtTime(detail.created_at)} · 操作员{" "}
              {detail.created_by} · 共 {detail.item_count}{" "}
              笔（打包瞬间冻结，后续办结不影响本明细）
            </p>
            <table>
              <thead>
                <tr>
                  <th>编号</th>
                  <th>探头代号</th>
                  <th>温度℃</th>
                  <th>打包时状态</th>
                </tr>
              </thead>
              <tbody>
                {detail.items.map((it) => (
                  <tr key={it.reading_id}>
                    <td>{it.reading_id}</td>
                    <td>{it.probe_id}</td>
                    <td>{it.temp_c}</td>
                    <td>
                      <span class="tag wait">
                        {frozenStatusText(it.status)}
                      </span>
                    </td>
                  </tr>
                ))}
                {detail.items.length === 0 && (
                  <tr>
                    <td colspan="4">包内无明细</td>
                  </tr>
                )}
              </tbody>
            </table>
          </>
        )}
      </div>
    </>
  );
}
