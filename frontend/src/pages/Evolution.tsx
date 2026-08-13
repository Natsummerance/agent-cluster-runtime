import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Empty,
  Form,
  InputNumber,
  message,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import { ExperimentOutlined, ReloadOutlined } from '@ant-design/icons';
import * as api from '../api/endpoints';
import { apiErrorMessage } from '../store/appStore';
import { useProjectParam } from '../hooks/useProjectParam';
import { useIntl } from '../i18n';
import PageHeader from '../components/PageHeader';
import ProjectSelector from '../components/ProjectSelector';
import type { EvolutionProposal } from '../api/types';

export default function Evolution() {
  const intl = useIntl();
  const [projectId, setProjectId] = useProjectParam();
  const [proposals, setProposals] = useState<EvolutionProposal[]>([]);
  const [loading, setLoading] = useState(false);
  const [actingId, setActingId] = useState<string | null>(null);
  const [genOpen, setGenOpen] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [retroResult, setRetroResult] = useState<unknown | null>(null);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const list = await api.fetchEvolutionProposals(projectId || undefined);
      setProposals(list);
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const act = useCallback(
    async (id: string, kind: 'apply' | 'rollback') => {
      setActingId(id);
      try {
        if (kind === 'apply') await api.applyEvolutionProposal(id);
        else await api.rollbackEvolutionProposal(id);
        message.success(
          kind === 'apply'
            ? intl.formatMessage({ id: 'evolution.applied', defaultMessage: 'Proposal applied' })
            : intl.formatMessage({ id: 'evolution.rolledBack', defaultMessage: 'Proposal rolled back' }),
        );
        void load();
      } catch (err) {
        message.error(apiErrorMessage(err));
      } finally {
        setActingId(null);
      }
    },
    [load, intl],
  );

  const generate = useCallback(async () => {
    try {
      const values = await form.validateFields();
      setGenerating(true);
      const result = await api.generateEvolutionProposals({
        project_id: projectId || undefined,
        min_evidence: values.min_evidence,
        limit: values.limit,
      });
      message.success(intl.formatMessage({ id: 'evolution.generated', defaultMessage: 'Proposal generation finished' }));
      setGenOpen(false);
      form.resetFields();
      void load();
      setRetroResult(result);
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) return;
      message.error(apiErrorMessage(err));
    } finally {
      setGenerating(false);
    }
  }, [form, projectId, load, intl]);

  const retro = useCallback(async () => {
    try {
      const result = await api.runEvolutionRetro({ project_id: projectId || undefined });
      setRetroResult(result);
      message.success(intl.formatMessage({ id: 'evolution.retroDone', defaultMessage: 'Retrospective finished' }));
      void load();
    } catch (err) {
      message.error(apiErrorMessage(err));
    }
  }, [projectId, load, intl]);

  const columns = [
    { title: 'ID', dataIndex: 'id', key: 'id', render: (v: string) => <Typography.Text className="mono">{v}</Typography.Text> },
    {
      title: intl.formatMessage({ id: 'evolution.col.title', defaultMessage: 'Title' }),
      dataIndex: 'title',
      key: 'title',
      render: (v: string | undefined, record: EvolutionProposal) => v ?? record.summary ?? '-',
    },
    {
      title: intl.formatMessage({ id: 'common.status', defaultMessage: 'Status' }),
      dataIndex: 'status',
      key: 'status',
      render: (v?: string) => (
        <Tag color={v === 'applied' ? 'green' : v === 'proposed' ? 'blue' : 'default'}>{v ?? '-'}</Tag>
      ),
    },
    {
      title: intl.formatMessage({ id: 'evolution.col.evidence', defaultMessage: 'Evidence' }),
      dataIndex: 'evidence',
      key: 'evidence',
      ellipsis: true,
      render: (v?: string) => v ?? '-',
    },
    {
      title: intl.formatMessage({ id: 'common.actions', defaultMessage: 'Actions' }),
      key: 'actions',
      render: (_: unknown, record: EvolutionProposal) => (
        <Space>
          <Button size="small" type="primary" loading={actingId === record.id} onClick={() => void act(record.id, 'apply')}>
            {intl.formatMessage({ id: 'evolution.apply', defaultMessage: 'Apply' })}
          </Button>
          <Popconfirm
            title={intl.formatMessage({
              id: 'evolution.confirmRollback',
              defaultMessage: 'Roll back this proposal?',
            })}
            onConfirm={() => void act(record.id, 'rollback')}
          >
            <Button size="small" loading={actingId === record.id}>
              {intl.formatMessage({ id: 'evolution.rollback', defaultMessage: 'Rollback' })}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div data-testid="evolution-page">
      <PageHeader
        title={intl.formatMessage({ id: 'evolution.header.title', defaultMessage: 'Evolution management' })}
        description={intl.formatMessage({
          id: 'evolution.header.desc',
          defaultMessage: 'Generate, review, apply and roll back workflow evolution proposals',
        })}
        actions={
          <Space>
            <Button icon={<ExperimentOutlined />} onClick={() => setGenOpen(true)} data-testid="generate-proposals-btn">
              {intl.formatMessage({ id: 'evolution.generate', defaultMessage: 'Generate proposals' })}
            </Button>
            <Button icon={<ReloadOutlined />} onClick={() => void retro()} data-testid="retro-btn">
              {intl.formatMessage({ id: 'evolution.retro', defaultMessage: 'Retrospective' })}
            </Button>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => void load()}
              aria-label={intl.formatMessage({
                id: 'evolution.refreshAria',
                defaultMessage: 'Refresh proposal list',
              })}
              data-testid="refresh-proposals-btn"
            />
          </Space>
        }
      />
      <div style={{ marginBottom: 16 }}>
        <ProjectSelector
          value={projectId || undefined}
          onChange={setProjectId}
          placeholder={intl.formatMessage({
            id: 'evolution.selectProject',
            defaultMessage: 'Select project (optional)',
          })}
        />
      </div>
      {retroResult !== null && (
        <Alert
          type="success"
          showIcon
          closable
          message={intl.formatMessage({ id: 'evolution.retroResult', defaultMessage: 'Retrospective result' })}
          description={<pre className="code-preview">{JSON.stringify(retroResult, null, 2)}</pre>}
          style={{ marginBottom: 16 }}
          data-testid="retro-result"
        />
      )}
      <Card>
        {proposals.length === 0 && !loading ? (
          <Empty
            description={intl.formatMessage({
              id: 'evolution.empty',
              defaultMessage: 'No evolution proposals; click "Generate proposals" or "Retrospective" to start',
            })}
          />
        ) : (
          <Table<EvolutionProposal>
            rowKey="id"
            columns={columns}
            dataSource={proposals}
            loading={loading}
            pagination={false}
            data-testid="proposals-table"
          />
        )}
      </Card>
      <Modal
        title={intl.formatMessage({ id: 'evolution.modal.title', defaultMessage: 'Generate evolution proposals' })}
        open={genOpen}
        onOk={generate}
        confirmLoading={generating}
        onCancel={() => setGenOpen(false)}
        okText={intl.formatMessage({ id: 'evolution.generate', defaultMessage: 'Generate' })}
        cancelText={intl.formatMessage({ id: 'common.cancel', defaultMessage: 'Cancel' })}
        data-testid="generate-modal"
      >
        <Form form={form} layout="vertical" initialValues={{ min_evidence: 2, limit: 5 }}>
          <Form.Item
            name="min_evidence"
            label={intl.formatMessage({ id: 'evolution.modal.minEvidence', defaultMessage: 'Minimum evidence' })}
          >
            <InputNumber min={1} max={20} style={{ width: '100%' }} data-testid="min-evidence-input" />
          </Form.Item>
          <Form.Item
            name="limit"
            label={intl.formatMessage({ id: 'evolution.modal.limit', defaultMessage: 'Proposal limit' })}
          >
            <InputNumber min={1} max={50} style={{ width: '100%' }} data-testid="limit-input" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
