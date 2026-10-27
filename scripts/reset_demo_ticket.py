"""把 s5 用到的工单重置成干净状态，方便反复演示升级流程。"""

import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

conn = sqlite3.connect("data/helpdesk.db")
conn.execute(
    "UPDATE tickets SET state = 'closed', labels = ? WHERE id = ?",
    ('["synthetic", "p3"]', "synthetic#0007"),
)
conn.commit()
print(list(conn.execute("select id,state,labels from tickets where id='synthetic#0007'")))
