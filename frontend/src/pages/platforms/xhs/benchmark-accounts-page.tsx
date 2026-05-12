import {
  DeleteOutlined,
  LinkOutlined,
  ReloadOutlined,
  RocketOutlined,
  ScanOutlined,
  SettingOutlined,
  FireOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Avatar,
  Button,
  Card,
  Col,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { PageHeader } from "../../../components/layout/app-shell";
import {
  configureBenchmarkAutoScan,
  crawlBenchmarkAccountPopularNotes,
  createBenchmarkAccount,
  deleteBenchmarkAccount,
  fetchAccounts,
  fetchBenchmarkAccounts,
  scanAndMonitorBenchmarkAccount,
} from "../../../lib/api";
import { formatShanghaiTime } from "../../../lib/time";
import { MAX_XHS_CRAWL_INTERVAL_SECONDS } from "../../../lib/xhs-crawl";
import type {
  BenchmarkAccountCrawlResult,
  BenchmarkAccountPopularNote,
  MonitoringTarget,
  PlatformAccount,
} from "../../../types";

const { Text } = Typography;

const cardStyle: React.CSSProperties = {
  background: "#1f1f1f",
  borderColor: "#303030",
};

function configNumber(target: MonitoringTarget | null, key: string, fallback: number): number {
  const value = target?.config?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function configString(target: MonitoringTarget | null, key: string, fallback = ""): string {
  const value = target?.config?.[key];
  return typeof value === "string" ? value : fallback;
}

function profileOf(target: MonitoringTarget): Record<string, unknown> {
  const config = target.config;
  const profile = config?.profile;
  return profile && typeof profile === "object" && !Array.isArray(profile) ? profile as Record<string, unknown> : {};
}

function profileText(target: MonitoringTarget, key: string, fallback = "-"): string {
  const value = profileOf(target)[key];
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function displayNameOf(target: MonitoringTarget): string {
  return profileText(target, "nickname", target.name || "未命名账号");
}

function avatarOf(target: MonitoringTarget): string {
  return profileText(target, "avatar_url", "");
}

function configBool(target: MonitoringTarget | null, key: string): boolean {
  return Boolean(target?.config?.[key]);
}

function formatTimestamp(timestamp?: number | null): string {
  if (!timestamp || !Number.isFinite(timestamp)) return "-";
  return new Date((timestamp > 10_000_000_000 ? timestamp : timestamp * 1000)).toLocaleString("zh-CN");
}

export function BenchmarkAccountsPage() {
  const navigate = useNavigate();
  const [form] = Form.useForm<{
    account_id: number;
    recent_months: number;
    max_notes: number;
    request_interval_seconds: number;
  }>();
  const [scanForm] = Form.useForm<{
    account_id: number;
    recent_hours: number;
    crawl_interval_minutes: number;
    request_interval_seconds: number;
  }>();
  const [autoScanForm] = Form.useForm<{
    enabled: boolean;
    account_id: number;
    scan_interval_hours: number;
    recent_hours: number;
    crawl_interval_minutes: number;
  }>();
  const [targets, setTargets] = useState<MonitoringTarget[]>([]);
  const [accounts, setAccounts] = useState<PlatformAccount[]>([]);
  const [newUrl, setNewUrl] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingAccounts, setIsLoadingAccounts] = useState(true);
  const [isAdding, setIsAdding] = useState(false);
  const [deletingIds, setDeletingIds] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const [activeTarget, setActiveTarget] = useState<MonitoringTarget | null>(null);
  const [isPopupModalOpen, setIsPopupModalOpen] = useState(false);
  const [isCrawling, setIsCrawling] = useState(false);
  const [crawlResult, setCrawlResult] = useState<BenchmarkAccountCrawlResult | null>(null);

  const [isScanModalOpen, setIsScanModalOpen] = useState(false);
  const [isScanning, setIsScanning] = useState(false);
  const [scanResult, setScanResult] = useState<{ scanned_links: number; new_monitoring_targets: number } | null>(null);

  const [isAutoScanModalOpen, setIsAutoScanModalOpen] = useState(false);
  const [isSavingAutoScan, setIsSavingAutoScan] = useState(false);

  const pcAccounts = useMemo(
    () => accounts.filter((account) => account.platform === "xhs" && account.sub_type === "pc"),
    [accounts],
  );

  const activePcAccounts = useMemo(
    () => pcAccounts.filter((account) => account.status === "active"),
    [pcAccounts],
  );

  const loadTargets = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await fetchBenchmarkAccounts();
      setTargets(result.items);
    } catch {
      setError("对标账号池加载失败。");
    } finally {
      setIsLoading(false);
    }
  }, []);

  const loadAccounts = useCallback(async () => {
    setIsLoadingAccounts(true);
    try {
      const items = await fetchAccounts("xhs");
      setAccounts(items);
    } catch {
      setAccounts([]);
    } finally {
      setIsLoadingAccounts(false);
    }
  }, []);

  useEffect(() => {
    void loadTargets();
    void loadAccounts();
  }, [loadTargets, loadAccounts]);

  function closeModals() {
    setActiveTarget(null);
    setIsPopupModalOpen(false);
    setCrawlResult(null);
    setIsScanModalOpen(false);
    setScanResult(null);
    setIsAutoScanModalOpen(false);
  }

  async function handleAdd() {
    const trimmed = newUrl.trim();
    if (!trimmed) return;
    setIsAdding(true);
    setError(null);
    setMessage(null);
    try {
      await createBenchmarkAccount(trimmed);
      setNewUrl("");
      setMessage("已加入对标账号池。");
      await loadTargets();
    } catch {
      setError("添加对标账号失败，请检查主页链接是否正确。");
    } finally {
      setIsAdding(false);
    }
  }

  async function handleDelete(targetId: number) {
    setDeletingIds((prev) => new Set(prev).add(targetId));
    setError(null);
    setMessage(null);
    try {
      await deleteBenchmarkAccount(targetId);
      setTargets((prev) => prev.filter((target) => target.id !== targetId));
      setMessage("已移出对标账号池。");
    } catch {
      setError("删除失败，请稍后重试。");
    } finally {
      setDeletingIds((prev) => {
        const next = new Set(prev);
        next.delete(targetId);
        return next;
      });
    }
  }

  function openCrawlModal(target: MonitoringTarget) {
    const preferredAccountId =
      typeof target.config?.crawler_account_id === "number"
        ? target.config.crawler_account_id
        : target.platform_account_id;
    const defaultAccountId =
      activePcAccounts.find((account) => account.id === preferredAccountId)?.id
      ?? activePcAccounts[0]?.id
      ?? pcAccounts[0]?.id;
    setActiveTarget(target);
    setCrawlResult(null);
    setIsPopupModalOpen(true);
    form.setFieldsValue({
      account_id: defaultAccountId,
      recent_months: configNumber(target, "recent_months", 1),
      max_notes: configNumber(target, "max_notes", 20),
      request_interval_seconds: configNumber(target, "request_interval_seconds", 1),
    });
  }

  async function handleCrawl() {
    if (!activeTarget) return;
    const values = await form.validateFields();
    setIsCrawling(true);
    setError(null);
    setMessage(null);
    setCrawlResult(null);
    try {
      const result = await crawlBenchmarkAccountPopularNotes(
        {
          target_id: activeTarget.id,
          account_id: values.account_id,
          recent_months: values.recent_months,
          max_notes: values.max_notes,
          request_interval_seconds: values.request_interval_seconds,
        },
        (msg) => setMessage(msg),
        (msg) => setError(msg),
      );
      if (result) {
        setCrawlResult(result);
        setTargets((prev) => prev.map((t) => (t.id === result.target.id ? result.target : t)));
        setActiveTarget(result.target);
        setMessage(`已将 ${result.imported_count} 篇历史爆款加入内容库。`);
      }
    } catch {
      setError("抓取爆款失败，请确认已绑定可用的 PC 账号。");
    } finally {
      setIsCrawling(false);
    }
  }

  function openScanModal(target: MonitoringTarget) {
    const preferredAccountId =
      typeof target.config?.last_scan_account_id === "number"
        ? target.config.last_scan_account_id
        : target.platform_account_id;
    const defaultAccountId =
      activePcAccounts.find((account) => account.id === preferredAccountId)?.id
      ?? activePcAccounts[0]?.id
      ?? pcAccounts[0]?.id;
    setActiveTarget(target);
    setScanResult(null);
    setIsScanModalOpen(true);
    scanForm.setFieldsValue({
      account_id: defaultAccountId,
      recent_hours: 168,
      crawl_interval_minutes: 60,
      request_interval_seconds: 1,
    });
  }

  async function handleScan() {
    if (!activeTarget) return;
    const values = await scanForm.validateFields();
    setIsScanning(true);
    setError(null);
    setMessage(null);
    try {
      const result = await scanAndMonitorBenchmarkAccount({
        target_id: activeTarget.id,
        account_id: values.account_id,
        recent_hours: values.recent_hours,
        crawl_interval_minutes: values.crawl_interval_minutes,
        request_interval_seconds: values.request_interval_seconds,
      });
      setScanResult(result);
      setTargets((prev) => prev.map((target) => (target.id === result.target.id ? result.target : target)));
      setActiveTarget(result.target);
      setMessage(`扫描到 ${result.scanned_links} 条链接，新增 ${result.new_monitoring_targets} 篇到竞品监控。`);
    } catch {
      setError("扫描并加入监控失败。");
    } finally {
      setIsScanning(false);
    }
  }

  function openAutoScanModal(target: MonitoringTarget) {
    const prefAccountId =
      typeof target.config?.scan_account_id === "number"
        ? target.config.scan_account_id
        : activePcAccounts[0]?.id;
    setActiveTarget(target);
    setIsAutoScanModalOpen(true);
    autoScanForm.setFieldsValue({
      enabled: configBool(target, "scan_enabled"),
      account_id: typeof target.config?.scan_account_id === "number" ? target.config.scan_account_id : prefAccountId,
      scan_interval_hours: configNumber(target, "scan_interval_hours", 6),
      recent_hours: configNumber(target, "scan_recent_hours", 168),
      crawl_interval_minutes: configNumber(target, "scan_crawl_interval_minutes", 60),
    });
  }

  async function handleAutoScanSave() {
    if (!activeTarget) return;
    const values = await autoScanForm.validateFields();
    setIsSavingAutoScan(true);
    setError(null);
    setMessage(null);
    try {
      const updated = await configureBenchmarkAutoScan({
        target_id: activeTarget.id,
        enabled: values.enabled,
        scan_interval_hours: values.scan_interval_hours,
        recent_hours: values.recent_hours,
        crawl_interval_minutes: values.crawl_interval_minutes,
        account_id: values.account_id,
      });
      setTargets((prev) => prev.map((target) => (target.id === updated.id ? updated : target)));
      setMessage("自动扫描设置已保存。");
      setIsAutoScanModalOpen(false);
    } catch {
      setError("保存自动扫描设置失败。");
    } finally {
      setIsSavingAutoScan(false);
    }
  }

  const resultColumns: ColumnsType<BenchmarkAccountPopularNote> = useMemo(
    () => [
      {
        title: "笔记",
        dataIndex: "title",
        key: "title",
        render: (_value, record) => (
          <Space direction="vertical" size={2}>
            <a href={record.note_url} target="_blank" rel="noreferrer">{record.title || record.note_id}</a>
            <Text type="secondary" style={{ fontSize: 12 }}>{record.author_name}</Text>
          </Space>
        ),
      },
      { title: "赞", dataIndex: "likes", key: "likes", width: 80 },
      { title: "藏", dataIndex: "collects", key: "collects", width: 80 },
      { title: "评", dataIndex: "comments", key: "comments", width: 80 },
      { title: "转", dataIndex: "shares", key: "shares", width: 80 },
      { title: "总互动", dataIndex: "engagement", key: "engagement", width: 100 },
      {
        title: "爆款倍数",
        dataIndex: "multiplier",
        key: "multiplier",
        width: 100,
        render: (value?: number | null) => (typeof value === "number" ? `${value.toFixed(2)}x` : "-"),
      },
      {
        title: "发布时间",
        dataIndex: "timestamp",
        key: "timestamp",
        width: 180,
        render: (value?: number | null) => formatTimestamp(value),
      },
    ],
    [],
  );

  return (
    <div>
      <PageHeader
        eyebrow="Benchmark Account Pool"
        title="对标账号池"
        description="维护小红书对标账号主页链接。抓取历史爆款直接入库，扫描最新发帖加入竞品监控以跟踪互动增速。"
        action={<Button icon={<ReloadOutlined />} onClick={() => { void loadTargets(); void loadAccounts(); }} loading={isLoading || isLoadingAccounts}>刷新</Button>}
      />

      <Card size="small" style={{ ...cardStyle, marginBottom: 24 }}>
        <Space wrap style={{ width: "100%" }}>
          <Input
            placeholder="粘贴小红书用户主页 URL"
            prefix={<LinkOutlined />}
            value={newUrl}
            onChange={(event) => setNewUrl(event.target.value)}
            onPressEnter={() => void handleAdd()}
            allowClear
            style={{ width: 460 }}
          />
          <Button type="primary" onClick={() => void handleAdd()} loading={isAdding}>添加到账号池</Button>
        </Space>
      </Card>

      {error && <Alert type="error" message={error} showIcon closable onClose={() => setError(null)} style={{ marginBottom: 16 }} />}
      {message && <Alert type="success" message={message} showIcon closable onClose={() => setMessage(null)} style={{ marginBottom: 16 }} />}

      {isLoading ? (
        <Card style={cardStyle}><Text type="secondary">正在加载对标账号池...</Text></Card>
      ) : targets.length === 0 ? (
        <Card style={cardStyle}>
          <Empty description="还没有对标账号，先在上方添加一个主页链接。" />
        </Card>
      ) : (
        <Row gutter={[16, 16]}>
          {targets.map((target) => {
            const scanEnabled = configBool(target, "scan_enabled");
            const lastScanAt = configString(target, "last_scan_at");
            const scanNextRun = configString(target, "scan_next_run_at");
            const scanLastNew = configNumber(target, "last_scan_new_targets", 0);

            return (
              <Col xs={24} lg={12} key={target.id}>
                <Card style={cardStyle} styles={{ body: { padding: 18 } }}>
                  <Space direction="vertical" size={10} style={{ width: "100%" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                      <div style={{ minWidth: 0, flex: 1, display: "flex", gap: 12 }}>
                        <Avatar src={avatarOf(target) || undefined} size={52} style={{ background: "#1668dc", flexShrink: 0, fontSize: 18 }}>
                          {displayNameOf(target).slice(0, 1).toUpperCase()}
                        </Avatar>
                        <div style={{ minWidth: 0, flex: 1 }}>
                          <Text strong style={{ display: "block" }} ellipsis={{ tooltip: displayNameOf(target) }}>{displayNameOf(target)}</Text>
                          <Text type="secondary" style={{ fontSize: 12, display: "block" }} ellipsis={{ tooltip: target.name }}>ID：{target.name || "-"}</Text>
                          <Text type="secondary" style={{ fontSize: 12 }} ellipsis={{ tooltip: target.value }}>{target.value}</Text>
                        </div>
                      </div>
                      <Space size={4}>
                        {scanEnabled && <Tag color="blue" icon={<ScanOutlined />}>自动扫描</Tag>}
                        <Tag color={target.status === "active" ? "green" : "default"}>{target.status === "active" ? "可抓取" : target.status}</Tag>
                      </Space>
                    </div>

                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
                        gap: 8,
                        padding: "10px 12px",
                        background: "#141414",
                        borderRadius: 8,
                      }}
                    >
                      <div style={{ textAlign: "center" }}>
                        <Text type="secondary" style={{ fontSize: 12 }}>粉丝</Text>
                        <div style={{ fontSize: 15, fontWeight: 600 }}>{profileText(target, "followers")}</div>
                      </div>
                      <div style={{ textAlign: "center" }}>
                        <Text type="secondary" style={{ fontSize: 12 }}>发帖</Text>
                        <div style={{ fontSize: 15, fontWeight: 600 }}>{profileText(target, "note_count")}</div>
                      </div>
                      <div style={{ textAlign: "center" }}>
                        <Text type="secondary" style={{ fontSize: 12 }}>关注</Text>
                        <div style={{ fontSize: 15, fontWeight: 600 }}>{profileText(target, "following")}</div>
                      </div>
                      <div style={{ textAlign: "center" }}>
                        <Text type="secondary" style={{ fontSize: 12 }}>获赞藏</Text>
                        <div style={{ fontSize: 15, fontWeight: 600 }}>{profileText(target, "likes")}</div>
                      </div>
                    </div>

                    <Space size="middle" wrap>
                      <Text type="secondary" style={{ fontSize: 12 }}>添加时间：{formatShanghaiTime(target.created_at)}</Text>
                      <Text type="secondary" style={{ fontSize: 12 }}>最近抓取爆款：{formatShanghaiTime(target.last_refreshed_at)}</Text>
                      <Text type="secondary" style={{ fontSize: 12 }}>小红书号：{profileText(target, "red_id")}</Text>
                    </Space>

                    {target.last_crawl_error && (
                      <Alert type="warning" showIcon message={target.last_crawl_error} />
                    )}

                    {(scanEnabled || (target.monitored_note_count ?? 0) > 0) && (
                      <div style={{ padding: "6px 10px", background: "#141414", borderRadius: 6 }}>
                        <Space wrap size={12}>
                          {(target.monitored_note_count ?? 0) > 0 && (
                            <Text type="secondary" style={{ fontSize: 12 }}>
                              监控中：{target.monitored_note_count} 篇
                            </Text>
                          )}
                          {scanEnabled && (
                            <>
                              <Text type="secondary" style={{ fontSize: 12 }}>
                                上次扫描：{lastScanAt ? formatShanghaiTime(lastScanAt) : "-"}
                              </Text>
                              <Text type="secondary" style={{ fontSize: 12 }}>
                                下次扫描：{scanNextRun ? formatShanghaiTime(scanNextRun) : "-"}
                              </Text>
                              {scanLastNew > 0 && (
                                <Text type="secondary" style={{ fontSize: 12 }}>
                                  上次新增：{scanLastNew} 篇
                                </Text>
                              )}
                            </>
                          )}
                        </Space>
                      </div>
                    )}

                    <Space wrap>
                      <Button type="primary" icon={<RocketOutlined />} onClick={() => openCrawlModal(target)} disabled={!activePcAccounts.length || isLoadingAccounts}>
                        抓取爆款
                      </Button>
                      <Button icon={<ScanOutlined />} onClick={() => openScanModal(target)} disabled={!activePcAccounts.length || isLoadingAccounts}>
                        扫描并监控
                      </Button>
                      <Button icon={<SettingOutlined />} onClick={() => openAutoScanModal(target)} disabled={!activePcAccounts.length || isLoadingAccounts}>
                        设置自动扫描
                      </Button>
                      <Popconfirm
                        title="确认移出该对标账号？"
                        onConfirm={() => void handleDelete(target.id)}
                        okText="删除"
                        cancelText="取消"
                      >
                        <Button danger icon={<DeleteOutlined />} loading={deletingIds.has(target.id)}>移出账号池</Button>
                      </Popconfirm>
                    </Space>
                  </Space>
                </Card>
              </Col>
            );
          })}
        </Row>
      )}

      {/* Crawl Popular Modal */}
      <Modal
        open={isPopupModalOpen}
        onCancel={() => { if (!isCrawling) { setIsPopupModalOpen(false); setActiveTarget(null); setCrawlResult(null); } }}
        width={980}
        title={`抓取历史爆款${activeTarget ? ` · ${activeTarget.name}` : ""}`}
        okText="开始抓取"
        onOk={() => void handleCrawl()}
        confirmLoading={isCrawling}
        cancelButtonProps={{ disabled: isCrawling }}
      >
        <Form form={form} layout="vertical">
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item label="抓取账号" name="account_id" rules={[{ required: true, message: "请选择抓取账号" }]}>
                <Select
                  placeholder="选择一个 PC 账号"
                  loading={isLoadingAccounts}
                  options={pcAccounts.map((account) => ({
                    value: account.id,
                    label: `${account.nickname || `PC 账号 ${account.id}`} · ${account.status}`,
                    disabled: account.status !== "active",
                  }))}
                />
              </Form.Item>
            </Col>
            <Col span={5}>
              <Form.Item label="时间范围" name="recent_months" rules={[{ required: true, message: "请输入最近 N 个月" }]}>
                <InputNumber min={1} max={24} addonAfter="个月" style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col span={5}>
              <Form.Item label="抓取总数" name="max_notes" rules={[{ required: true, message: "请输入抓取总数" }]}>
                <InputNumber min={1} max={200} addonAfter="篇" style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item label="抓取间隔" name="request_interval_seconds" rules={[{ required: true, message: "请输入抓取间隔" }]}>
                <InputNumber min={0} max={MAX_XHS_CRAWL_INTERVAL_SECONDS} step={0.5} addonAfter="秒" style={{ width: "100%" }} />
              </Form.Item>
            </Col>
          </Row>
        </Form>

        {!activePcAccounts.length && !isLoadingAccounts && (
          <Alert type="warning" showIcon style={{ marginBottom: 16 }} message="还没有可用的 PC 账号，请先到账号矩阵绑定一个 PC 账号。" />
        )}

        {crawlResult && (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Row gutter={12}>
              <Col span={6}><Card style={cardStyle}><Statistic title="候选链接" value={crawlResult.candidate_count} /></Card></Col>
              <Col span={6}><Card style={cardStyle}><Statistic title="详情成功" value={crawlResult.crawled_count} /></Card></Col>
              <Col span={6}><Card style={cardStyle}><Statistic title="判定爆款" value={crawlResult.popular_count} /></Card></Col>
              <Col span={6}><Card style={cardStyle}><Statistic title="已入内容库" value={crawlResult.imported_count} /></Card></Col>
            </Row>
            <Alert
              type={crawlResult.imported_count > 0 ? "success" : "info"}
              showIcon
              message={
                crawlResult.imported_count > 0
                  ? `已将 ${crawlResult.imported_count} 篇历史爆款加入内容库。`
                  : "本次未筛出历史爆款笔记。"
              }
              action={<Button type="link" onClick={() => navigate("/platforms/xhs/library")}>前往内容库</Button>}
            />
            <Table<BenchmarkAccountPopularNote>
              dataSource={crawlResult.items}
              columns={resultColumns}
              rowKey="note_id"
              size="small"
              pagination={false}
              locale={{ emptyText: "暂无爆款笔记" }}
              scroll={{ x: 900, y: 360 }}
            />
          </Space>
        )}
      </Modal>

      {/* Scan & Monitor Modal */}
      <Modal
        open={isScanModalOpen}
        onCancel={() => { if (!isScanning) { setIsScanModalOpen(false); setActiveTarget(null); setScanResult(null); } }}
        width={640}
        title={`扫描并加入竞品监控${activeTarget ? ` · ${activeTarget.name}` : ""}`}
        okText="开始扫描"
        onOk={() => void handleScan()}
        confirmLoading={isScanning}
        cancelButtonProps={{ disabled: isScanning }}
      >
        <Form form={scanForm} layout="vertical">
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item label="抓取账号" name="account_id" rules={[{ required: true, message: "请选择抓取账号" }]}>
                <Select
                  placeholder="选择一个 PC 账号"
                  loading={isLoadingAccounts}
                  options={pcAccounts.map((account) => ({
                    value: account.id,
                    label: `${account.nickname || `PC 账号 ${account.id}`} · ${account.status}`,
                    disabled: account.status !== "active",
                  }))}
                />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="扫描时间窗口" name="recent_hours" rules={[{ required: true, message: "请输入小时数" }]}>
                <InputNumber min={1} max={720} addonAfter="小时" style={{ width: "100%" }} />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item label="监控刷新间隔" name="crawl_interval_minutes" rules={[{ required: true, message: "请输入刷新间隔" }]}>
                <InputNumber min={10} max={1440} addonAfter="分钟" style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="抓取间隔" name="request_interval_seconds" rules={[{ required: true, message: "请输入抓取间隔" }]}>
                <InputNumber min={0} max={MAX_XHS_CRAWL_INTERVAL_SECONDS} step={0.5} addonAfter="秒" style={{ width: "100%" }} />
              </Form.Item>
            </Col>
          </Row>
        </Form>

        {!activePcAccounts.length && !isLoadingAccounts && (
          <Alert type="warning" showIcon style={{ marginBottom: 16 }} message="还没有可用的 PC 账号，请先到账号矩阵绑定一个 PC 账号。" />
        )}

        {scanResult && (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Row gutter={12}>
              <Col span={12}><Card style={cardStyle}><Statistic title="扫描到链接" value={scanResult.scanned_links} /></Card></Col>
              <Col span={12}><Card style={cardStyle}><Statistic title="新增竞品监控" value={scanResult.new_monitoring_targets} /></Card></Col>
            </Row>
            <Alert
              type={scanResult.new_monitoring_targets > 0 ? "success" : "info"}
              showIcon
              message={
                scanResult.new_monitoring_targets > 0
                  ? `已将 ${scanResult.new_monitoring_targets} 篇笔记加入竞品监控，将在后台按间隔自动刷新判定。`
                  : "未扫描到新发笔记。"
              }
              action={<Button type="link" onClick={() => navigate("/platforms/xhs/benchmarks")}>前往竞品监控</Button>}
            />
          </Space>
        )}
      </Modal>

      {/* Auto Scan Settings Modal */}
      <Modal
        open={isAutoScanModalOpen}
        onCancel={() => { setIsAutoScanModalOpen(false); setActiveTarget(null); }}
        width={640}
        title={`自动扫描设置${activeTarget ? ` · ${activeTarget.name}` : ""}`}
        okText="保存设置"
        onOk={() => void handleAutoScanSave()}
        confirmLoading={isSavingAutoScan}
      >
        <Form form={autoScanForm} layout="vertical">
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item label="启用自动扫描" name="enabled" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="扫描周期" name="scan_interval_hours" rules={[{ required: true, message: "请输入扫描周期" }]}>
                <InputNumber min={1} max={168} addonAfter="小时" style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="抓取账号" name="account_id" rules={[{ required: true, message: "请选择抓取账号" }]}>
                <Select
                  placeholder="选择一个 PC 账号"
                  loading={isLoadingAccounts}
                  options={pcAccounts.map((account) => ({
                    value: account.id,
                    label: `${account.nickname || `PC 账号 ${account.id}`} · ${account.status}`,
                    disabled: account.status !== "active",
                  }))}
                />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item label="扫描时间窗口" name="recent_hours" rules={[{ required: true, message: "请输入小时数" }]}>
                <InputNumber min={1} max={720} addonAfter="小时" style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="监控刷新间隔" name="crawl_interval_minutes" rules={[{ required: true, message: "请输入刷新间隔" }]}>
                <InputNumber min={10} max={1440} addonAfter="分钟" style={{ width: "100%" }} />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Modal>
    </div>
  );
}
