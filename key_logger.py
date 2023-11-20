#!/usr/bin/env python3


import logging
from pynput.keyboard import Key, Listener



SEND_LOGS_TO_SQLITE = True
SEND_LOGS_TO_FILE = False

LOG_FILE_NAME = 'key_log.txt'
SQLITE_FILE_NAME = 'key_log.sqlite'



logging.basicConfig(
    # level=logging.DEBUG,
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d : %(levelname)-5s : %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

if SEND_LOGS_TO_SQLITE:
  from datetime import datetime
  import sqlite3

if SEND_LOGS_TO_FILE:
  logging.info(f'File used for logging: {LOG_FILE_NAME}')

LOCKED_IN_GARBAGE_COLLECTION_LIMIT = 5

MODIFIER_KEYS = [
    Key.alt,
    Key.alt_r,
    Key.alt_l,
    Key.cmd,
    Key.cmd_r,
    Key.cmd_l,
    Key.ctrl,
    Key.ctrl_r,
    Key.ctrl_l,
    Key.shift,
    Key.shift_r,
    Key.shift_l,
]

IGNORED_KEYS = []

REMAP = {
    Key.alt_r: Key.alt,
    Key.alt_l: Key.alt,
    Key.ctrl_r: Key.ctrl,
    Key.ctrl_l: Key.ctrl,
    Key.cmd_r: Key.cmd,
    Key.cmd_l: Key.cmd,
    Key.shift_r: Key.shift,
    Key.shift_l: Key.shift,
}

keys_currently_down = []



def setup_sqlite_database():
 
  global db_connection
  global db_cursor
  db_connection = sqlite3.connect(
      SQLITE_FILE_NAME,
      check_same_thread=False,
      # The same thread check is off since the keyboard listener works
      # in a spawned thread (a decision of the pynput library) separate
      # from this python script.
  )
  db_cursor = db_connection.cursor()
  logging.debug('SQLite connection and cursor created')

  db_cursor.execute("""
      CREATE TABLE IF NOT EXISTS key_log
      (time_utc TEXT, key_code TEXT)
  """)
  logging.debug('SQLite logging table created')

  db_cursor.execute('DROP VIEW IF EXISTS key_counts')
  db_cursor.execute("""
    CREATE VIEW IF NOT EXISTS key_counts AS
    WITH frequencies AS (
        SELECT key_code, count(*) AS count,
            (count(*) * 1.0) / (SELECT count(*) FROM key_log) AS frequency
        FROM key_log
        GROUP BY 1
    )
    SELECT *, SUM(frequency) OVER (
        ORDER BY frequency DESC ROWS UNBOUNDED PRECEDING
    ) AS cumulative_frequency
    FROM frequencies
    ORDER BY frequency DESC, key_code
  """)
  logging.debug('SQLite key_counts view created')

  db_cursor.execute('DROP VIEW IF EXISTS bigram_counts')
  db_cursor.execute("""
    CREATE VIEW IF NOT EXISTS bigram_counts AS
    WITH raw_bigram_data AS
    (
      SELECT key_code, lag(key_code) OVER (ORDER BY time_utc) AS key_code_lag_1
      FROM key_log
    )
    , bigram_counts AS
    (
      SELECT key_code_lag_1 || ' ' || key_code AS bigram, count(*) AS count
      FROM raw_bigram_data
      WHERE true
        AND key_code IS NOT NULL
        AND key_code_lag_1 IS NOT NULL
        AND key_code NOT LIKE '%+%'
        AND key_code_lag_1 NOT LIKE '%+%'
      GROUP BY 1
    )
    , bigram_frequencies AS
    (
      SELECT *,
        (1.0* count ) / (SELECT sum(count) FROM bigram_counts) AS frequency
      FROM bigram_counts
    )
    SELECT *, SUM(frequency) OVER (
        ORDER BY frequency DESC ROWS UNBOUNDED PRECEDING
    ) AS cumulative_frequency
    FROM bigram_frequencies
    GROUP BY bigram
    ORDER BY cumulative_frequency, count DESC, bigram
  """)
  logging.debug('SQLite bigram_counts view created')

  db_cursor.execute('DROP VIEW IF EXISTS trigram_counts')
  db_cursor.execute("""
    CREATE VIEW IF NOT EXISTS trigram_counts AS
    WITH raw_trigram_data AS
    (
      SELECT
        key_code,
        lag(key_code) OVER (ORDER BY time_utc) AS key_code_lag_1,
        lag(key_code, 2) OVER (ORDER BY time_utc) AS key_code_lag_2
      FROM key_log
    )
    , trigram_counts AS
    (
      SELECT
        key_code_lag_2 || ' ' || key_code_lag_1 || ' ' || key_code AS trigram,
        count(*) AS count
      FROM raw_trigram_data
      WHERE true
        AND key_code IS NOT NULL
        AND key_code_lag_1 IS NOT NULL
        AND key_code_lag_2 IS NOT NULL
        AND key_code NOT LIKE '%+%'
        AND key_code_lag_1 NOT LIKE '%+%'
        AND key_code_lag_2 NOT LIKE '%+%'
      GROUP BY 1
    )
    , trigram_frequencies AS
    (
      SELECT *,
        (1.0* count ) / (SELECT sum(count) FROM trigram_counts) AS frequency
      FROM trigram_counts
    )
    SELECT *, SUM(frequency) OVER (
        ORDER BY frequency DESC ROWS UNBOUNDED PRECEDING
    ) AS cumulative_frequency
    FROM trigram_frequencies
    GROUP BY trigram
    ORDER BY cumulative_frequency, count DESC, trigram
  """)
  logging.debug('SQLite trigram_counts view created')

  db_connection.commit()
  logging.info(f'SQLite database set up: {SQLITE_FILE_NAME}')


def log(key):

  modifiers_down = [k for k in keys_currently_down if k in MODIFIER_KEYS]
  if list(set([
      Key.shift if k in [Key.shift, Key.shift_l, Key.shift_r] else k
      for k in modifiers_down
  ])) == [Key.shift] and key_is_a_symbol(key):
    modifiers_down = []
  log_entry = ' + '.join(
      sorted([key_to_str(k) for k in modifiers_down])
      + [key_to_str(key)]
  )
  logging.info(f'key: {log_entry}')

  if SEND_LOGS_TO_SQLITE:
    row_values = (datetime.utcnow().isoformat(), log_entry)
    db_cursor.execute(
        'INSERT INTO key_log VALUES (?, ?)',
        row_values
    )
    db_connection.commit()
    logging.debug(f'logged to SQLite: {row_values}')

  if SEND_LOGS_TO_FILE:
    with open(LOG_FILE_NAME, 'a') as log_file:  # append mode
      log_file.write(f'{log_entry}\n')
      logging.debug(f'logged to file: {log_entry}')




def key_is_a_symbol(key):
  return str(key)[0:4] != 'Key.'


def key_to_str(key):
 
  s = str(key)
  if not key_is_a_symbol(key):
    s = f'<{s[4:]}>'
  else:
    s = s.encode('latin-1', 'backslashreplace').decode('unicode-escape')
    s = s[1:-1]  # trim the leading and trailing quotes
  return s


def key_down(key):

  if key in keys_currently_down:
    return

  keys_currently_down.append(key)
  logging.debug(
      f'key down : {key_to_str(key)} : '
      f'{[key_to_str(k) for k in keys_currently_down]}'
  )
  if key not in MODIFIER_KEYS:
    log(key)


def key_up(key):

  global keys_currently_down

  try:
    keys_currently_down.remove(key)
  except ValueError:
    logging.warning(f'{key_to_str(key)} up event without a paired down event')
    if len(keys_currently_down) >= LOCKED_IN_GARBAGE_COLLECTION_LIMIT:
      logging.debug('key-down count is above locked-in limit')
      number_of_modifiers_down = len([
          k for k in keys_currently_down if k in MODIFIER_KEYS
      ])
      if number_of_modifiers_down == 0:
        logging.debug(
            'clearing locked-in keys-down: '
            f'{[key_to_str(k) for k in keys_currently_down]}'
        )
        keys_currently_down = []

  logging.debug(
      f'key up  : {key_to_str(key)} : '
      f'{[key_to_str(k) for k in keys_currently_down]}'
  )


def preprocess(key, f):

  k = key
  if key in REMAP:
    k = REMAP[key]
    logging.debug(f'remapped key {key_to_str(key)} -> {key_to_str(k)}')

  if k in IGNORED_KEYS:
    logging.debug(f'ignoring key: {key_to_str(k)}')
    return

  return f(k)




def main():
  logging.info('getting set up')
  if SEND_LOGS_TO_SQLITE:
    setup_sqlite_database()

  with Listener(
      on_press=(lambda key: preprocess(key, key_down)),
      on_release=(lambda key: preprocess(key, key_up)),
  ) as listener:
    logging.info('starting to listen for keyboard events')
    listener.join()


if __name__ == '__main__':
  main()
