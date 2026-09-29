import { Card, Empty, Typography } from 'antd';

import type { Section } from './navigation';

const { Paragraph, Text } = Typography;

/**
 * What an unbuilt section shows.
 *
 * A blank page or a routing error would read as a bug; naming the change that
 * will fill the section makes the gap legible instead.
 */
export default function Placeholder({ section }: { section: Section }) {
  return (
    <Card>
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <div>
            <Paragraph style={{ marginBottom: 4 }}>
              <Text strong>{section.label}</Text> 分区尚未实现
            </Paragraph>
            <Paragraph type="secondary" style={{ marginBottom: 0 }}>
              该分区由后续变更提供，届时会在此处出现对应页面。
            </Paragraph>
          </div>
        }
      />
    </Card>
  );
}
