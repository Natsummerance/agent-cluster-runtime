import { Component, type ReactNode } from 'react';
import { Button, Result } from 'antd';
import { FormattedMessage } from 'react-intl';

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error('[workbench] render error:', error);
  }

  render() {
    if (this.state.error) {
      return (
        <Result
          status="error"
          title={<FormattedMessage id="errorBoundary.title" defaultMessage="Page rendering error" />}
          subTitle={this.state.error.message}
          extra={
            <Button type="primary" onClick={() => this.setState({ error: null })}>
              <FormattedMessage id="common.retry" defaultMessage="Retry" />
            </Button>
          }
        />
      );
    }
    return this.props.children;
  }
}
