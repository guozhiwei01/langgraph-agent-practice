"""演示 invoke 的底层原理"""

# 1. 定义一个普通函数
def add(a: int, b: int) -> int:
    print(f"add 函数被调用了！a={a}, b={b}")
    return a + b


# 2. 模拟 LangChain 的 ToolWrapper
class SimpleTool:
    def __init__(self, func):
        self.func = func  # 保存原始函数
        self.name = func.__name__

    def invoke(self, args: dict):
        """这就是 invoke 的实现！"""
        print(f"\n=== invoke 被调用 ===")
        print(f"接收到的参数（字典）: {args}")

        # 关键：用 ** 解包字典
        result = self.func(**args)
        #                  ^^^^^^
        # 这里就是魔法！

        print(f"返回结果: {result}")
        return result


# 3. 包装函数
tool = SimpleTool(add)

print("=" * 50)
print("方式1: 使用 invoke（大模型方式）")
print("=" * 50)
result1 = tool.invoke({"a": 3, "b": 4})


print("\n" + "=" * 50)
print("方式2: 直接调用（传统方式）")
print("=" * 50)
result2 = add(a=3, b=4)


print("\n" + "=" * 50)
print("方式3: 手动解包（等价写法）")
print("=" * 50)
args_dict = {"a": 3, "b": 4}
result3 = add(**args_dict)  # ** 解包字典


print("\n" + "=" * 50)
print(f"三种方式的结果：")
print(f"result1 (invoke):  {result1}")
print(f"result2 (直接调用): {result2}")
print(f"result3 (手动解包): {result3}")
print(f"是否相等: {result1 == result2 == result3}")
print("=" * 50)
