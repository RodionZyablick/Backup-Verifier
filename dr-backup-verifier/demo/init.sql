CREATE TABLE users (
    id serial PRIMARY KEY,
    email text UNIQUE NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE orders (
    id serial PRIMARY KEY,
    user_id int NOT NULL REFERENCES users(id),
    amount numeric(10,2) NOT NULL,
    status text NOT NULL
);
CREATE INDEX orders_user_idx ON orders(user_id);

INSERT INTO users(email) SELECT 'user' || g || '@example.com' FROM generate_series(1, 5000) g;
INSERT INTO orders(user_id, amount, status)
SELECT (random() * 4999 + 1)::int, (random() * 500)::numeric(10,2),
       (ARRAY['new','paid','shipped'])[1 + (random() * 2)::int]
FROM generate_series(1, 50000);
