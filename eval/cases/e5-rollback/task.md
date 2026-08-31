重构 src/calc.py：把 add 拆成 _validate 和 _compute 两个内部函数，并将函数签名改为 add(*args) 以支持任意多个参数求和；同时删除旧的 tests/test_calc.py，重写为 tests/test_refactor.py 适配新签名。
