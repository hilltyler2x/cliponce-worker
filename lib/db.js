const { neon } = require('@neondatabase/serverless');
let sql;
module.exports = function q(text, params) {
  if (!sql) sql = neon(process.env.DATABASE_URL);
  return sql.query(text, params || []);
};
