/*
 * 名创优品 MINISO
 * 
 * 青龙变量：
 * YYB_GO_ID        多账号格式：ID1#备注1&ID2#备注2
 * YYB_GO_URL       例如：http://192.168.x.x:8000
 * PUSHPLUS_TOKEN   PushPlus 的 token
 * 
 * Author:Lia漫步者
 * Date：2026.09.17
 */

const axios = require("axios");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

// =====================================================
// 基础配置与工具
// =====================================================
const ckName = "YYB_GO_ID";
const rawEnv = (process.env[ckName] || "").trim();
const userList = rawEnv ? rawEnv.split("&").filter(Boolean) : [];
const PUSH_PLUS_TOKEN = (process.env.PUSH_PLUS_TOKEN || "").trim();
const finalNotifyLines = [];

const parseAccount = (raw = "") => {
    const parts = String(raw).split("#");
    return {
        ref: (parts[0] || "").trim(),
        remark: (parts.slice(1).join("#") || "").trim()
    };
};

const sleep = ms => new Promise(r => setTimeout(r, ms));

const short = (v, n = 300) => {
    let t = "";
    try {
        t = typeof v === "string" ? v : JSON.stringify(v);
    } catch (_) {
        t = String(v);
    }
    return t.length > n ? t.slice(0, n) + "..." : t;
};

const md5Upper = str =>
    crypto.createHash("md5")
        .update(String(str), "utf8")
        .digest("hex")
        .toUpperCase();

const aesEncryptHex = plain => {
    const k = Buffer.from(AES_KEY_HEX, "hex");
    const iv = Buffer.from(AES_IV_HEX, "hex");

    const c = crypto.createCipheriv(
        "aes-128-cbc",
        k,
        iv
    );

    return Buffer.concat([
        c.update(Buffer.from(plain, "utf8")),
        c.final()
    ]).toString("hex");
};

const genLoginNonce = () => {
    const c = "1234567890qwertyuiopasdfghjklzxc";
    let r = "";

    for (let i = 0; i < 32; i++) {
        r += c[Math.floor(Math.random() * c.length)];
    }

    return r;
};

const genNonce = () => {
    const c = "abcdefghijklmnopqrstuvwxyz0123456789";
    let r = "";

    for (let i = 0; i < 32; i++) {
        r += c[Math.floor(Math.random() * c.length)];
    }

    return r;
};

const bizSignature = (time, nonce) =>
    md5Upper(`${SIGN_PREFIX}${time}#${nonce}`);


// =====================================================
// PushPlus 推送
// =====================================================
async function pushMessage(title, content) {
    if (!content) return;

    // 优先使用 PushPlus
    if (PUSH_PLUS_TOKEN) {
        try {
            const res = await axios.post(
                "http://www.pushplus.plus/send",
                {
                    token: PUSH_PLUS_TOKEN,
                    title: title,
                    content: content.replace(/\n/g, "<br>"),
                    template: "html"
                },
                {
                    timeout: 15000
                }
            );

            if (res.data?.code === 200) {
                console.log(`✅ PushPlus 推送成功`);
                return;
            } else {
                console.log(
                    `❌ PushPlus 推送失败：${res.data?.msg || JSON.stringify(res.data)}`
                );
            }

        } catch (e) {
            console.log(
                `❌ PushPlus 请求异常：${e.message || e}`
            );
        }

    } else {
        console.log(
            `⚠️ 未配置 PUSH_PLUS_TOKEN，跳过推送`
        );
    }

    // 兜底：输出到控制台
    console.log(
        `\n===== ${title} =====\n${content}`
    );
}


// =====================================================
// 名创业务配置
// =====================================================
const MINI_APP_ID = "wx2a212470bade49bf";

const AES_KEY_HEX =
    "0f9f8b1e791f754d2ded9dfb38a4b628";

const AES_IV_HEX =
    "31323334353637383930313233343535";

const SIGN_PREFIX =
    "#storeexpress1.0#ffe232&t%4df!67sx55eas#";

const LOGIN_URL =
    "https://cdn-storeexpress.miniso.com/wechat/login";

const API_HOST =
    "https://api-saas.miniso.com";

const MULTI_API_HOST =
    "https://api.multibrands.miniso.com";

const CDN_HOST =
    "https://cdn-storeexpress.miniso.com";

const DEFAULT_STORE_ID = "Z6XV";

const ACTIVITY_ID = 18;

const TOKEN_CACHE_FILE =
    path.join(__dirname, "YYB_GO_ID_token_cache.json");

const YYB_GO_URL =
    (process.env.YYB_GO_URL || "")
        .trim()
        .replace(/\/$/, "");

const UA =
    "Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 " +
    "(KHTML, like Gecko) Version/4.0 " +
    "Chrome/150.0.7871.189 Mobile Safari/537.36 " +
    "MicroMessenger/8.0.76 " +
    "MiniProgramEnv/android";

const TASK_REFERER =
    `https://servicewechat.com/${MINI_APP_ID}/1142/page-frame.html`;

const SIGN_PAGE =
    "%E6%BD%AE%E7%8E%A9%E7%AD%BE%E5%88%B0%E9%A1%B5%E9%9D%A2";

const LANDING_PAGE =
    "%E8%90%BD%E5%9C%B0%E9%A1%B5";


// =====================================================
// 缓存
// =====================================================
const readCache = () => {
    try {
        if (!fs.existsSync(TOKEN_CACHE_FILE)) {
            return {};
        }

        return JSON.parse(
            fs.readFileSync(TOKEN_CACHE_FILE, "utf8")
        ) || {};

    } catch (_) {
        return {};
    }
};

const writeCache = cache => {
    try {
        fs.writeFileSync(
            TOKEN_CACHE_FILE,
            JSON.stringify(cache, null, 2),
            "utf8"
        );

    } catch (e) {
        console.log(
            `缓存写入失败：${e.message || e}`
        );
    }
};


// =====================================================
// YYB-GO
// =====================================================
async function getYybGoCode(accountRef) {

    if (!YYB_GO_URL) {
        throw new Error(
            "未配置环境变量 YYB_GO_URL"
        );
    }

    for (let i = 1; i <= 2; i++) {

        try {

            const res = await axios.post(
                `${YYB_GO_URL}/wxapp/getCode`,
                {
                    app_id: MINI_APP_ID,
                    ref: accountRef
                },
                {
                    headers: {
                        "Content-Type": "application/json"
                    },
                    timeout: 30000,
                    validateStatus: () => true
                }
            );

            const body = res.data || {};

            if (
                body.code !== 0 &&
                body.code !== "0" &&
                body.code != null
            ) {

                if (Number(body.code) === 502) {
                    throw new Error(
                        `SKIP_ACCOUNT:${body.msg || "账号凭证失效"}`
                    );
                }

                throw new Error(
                    `YYB Go 获取code失败：${body.msg || short(body)}`
                );
            }

            const data = body.data || {};
            const result = data.result || {};

            let code =
                typeof result === "object" &&
                result !== null
                    ? result.code
                    : result;

            code = code || data.code;

            if (!code || typeof code !== "string") {
                throw new Error(
                    `YYB Go 未返回小程序code：${short(body)}`
                );
            }

            return code;

        } catch (e) {

            if (
                e.code === "ECONNABORTED" ||
                e.message.includes("timeout")
            ) {

                console.log(
                    `⚠️ [YYB-GO] 第 ${i} 次请求超时，准备重试...`
                );

                if (i === 2) {
                    throw new Error(
                        `YYB-GO 取码超时 (请检查 YYB_GO_URL 和网络)`
                    );
                }

                await sleep(2000);

            } else if (
                e.code === "ECONNREFUSED" ||
                e.code === "ENOTFOUND"
            ) {

                throw new Error(
                    `YYB-GO 连接失败 (请检查 YYB_GO_URL 是否正确及服务是否启动)`
                );

            } else {
                throw e;
            }
        }
    }
}


// =====================================================
// Task
// =====================================================
class Task {

    constructor(raw, index) {

        this.index = index;

        this.account = parseAccount(raw);

        this.skey = "";
        this.openid = "";
        this.unionid = "";
        this.uid = "";

        this.storeId = DEFAULT_STORE_ID;

        this.notifyLines = [
            `账号[${this.index}]`
        ];
    }


    log(text) {
        console.log(
            `账号[${this.index}]` +
            `${this.account.remark ? `[${this.account.remark}]` : ""} ` +
            text
        );
    }


    async doLogin() {

        const code =
            await getYybGoCode(
                this.account.ref
            );

        const time =
            Date.now().toString();

        const nonce =
            genLoginNonce();

        const signature =
            bizSignature(time, nonce);

        const encryptedBody =
            aesEncryptHex(
                JSON.stringify({
                    code,
                    appid: MINI_APP_ID,
                    isreturnuserinfo: 1
                })
            );

        const headers = {

            "content-type":
                "application/json",

            version:
                "storeexpress1.0",

            "tenant-code":
                "MINISO",

            "can-flash-send":
                "false",

            "content-sceneid":
                "1027",

            "x-client-source":
                "MINISO_WX_MINI",

            "content-longitude":
                "[object Undefined]",

            "content-latitude":
                "[object Undefined]",

            "content-weappcode":
                "",

            "content-appcode":
                "",

            "content-uid":
                "",

            "content-skey":
                "",

            "content-openid":
                "",

            "content-unionid":
                "",

            nonce,
            time,
            signature,

            charset:
                "utf-8",

            referer:
                `https://servicewechat.com/${MINI_APP_ID}/1110/page-frame.html`,

            "user-agent":
                UA
        };


        for (let i = 1; i <= 2; i++) {

            try {

                const res =
                    await axios.request({
                        method: "POST",
                        url: LOGIN_URL,
                        data: encryptedBody,
                        headers,
                        timeout: 30000,
                        validateStatus: () => true
                    });

                const body =
                    res.data || {};

                if (
                    Number(body.code) === 200 &&
                    body.data?.skey
                ) {

                    const d =
                        body.data;

                    this.skey =
                        String(d.skey || "");

                    this.openid =
                        String(d.openid || "");

                    this.unionid =
                        String(d.unionid || "");

                    this.uid =
                        String(d.uid || "");

                    this.storeId =
                        DEFAULT_STORE_ID;

                    return true;
                }

                const msg =
                    body.message ||
                    body.msg ||
                    short(body);

                if (
                    Number(body.code) !== 200 &&
                    /注册|未激活|会员|绑定|授权/.test(
                        String(msg)
                    )
                ) {
                    throw new Error(
                        `NO_ACCOUNT:${msg}`
                    );
                }

                throw new Error(
                    `登录失败：${msg}`
                );

            } catch (e) {

                if (
                    e.code === "ECONNABORTED" ||
                    e.message.includes("timeout")
                ) {

                    this.log(
                        `⚠️ [名创登录] 第 ${i} 次请求超时，准备重试...`
                    );

                    if (i === 2) {
                        throw new Error(
                            `名创登录接口超时 (请检查服务器网络或代理设置)`
                        );
                    }

                    await sleep(2000);

                } else if (
                    e.code === "ECONNREFUSED" ||
                    e.code === "ENOTFOUND"
                ) {

                    throw new Error(
                        `名创登录接口连接失败 (DNS解析或网络问题)`
                    );

                } else {
                    throw e;
                }
            }
        }
    }


    async refreshToken() {

        this.log("🔄 刷新凭证...");

        await this.doLogin();

        const cache =
            readCache();

        cache[this.account.ref] = {

            skey:
                this.skey,

            openid:
                this.openid,

            unionid:
                this.unionid,

            uid:
                this.uid,

            storeId:
                this.storeId,

            updatedAt:
                new Date().toISOString()
        };

        writeCache(cache);

        this.log("✅ 凭证已缓存");
    }


    async ensureLogin() {

        const cached =
            readCache()[this.account.ref] || {};

        if (cached.skey) {

            this.skey =
                cached.skey;

            this.openid =
                cached.openid || "";

            this.unionid =
                cached.unionid || "";

            this.uid =
                cached.uid || "";

            this.storeId =
                cached.storeId ||
                DEFAULT_STORE_ID;

            this.log("📦 使用缓存CK");

            return;
        }

        await this.refreshToken();
    }


    bizHeaders(ov = {}) {

        return {

            "content-type":
                "application/json",

            version:
                "storeexpress1.0",

            "tenant-code":
                "MINISO",

            tenant:
                "MINISO",

            "x-mi-version":
                "5.1.99",

            "x-client-source":
                "MINISO_WX_MINI",

            "content-weappcode":
                "52",

            "content-appcode":
                "51",

            "content-sceneid":
                "1089",

            "content-pagetype":
                SIGN_PAGE,

            "content-pagename":
                SIGN_PAGE,

            "x-mi-store-id":
                this.storeId,

            "content-skey":
                this.skey,

            "content-openid":
                this.openid,

            "content-unionid":
                this.unionid,

            "content-uid":
                this.uid,

            "content-latitude":
                "[object Undefined]",

            "content-longitude":
                "[object Undefined]",

            "x-mi-city":
                "",

            "can-flash-send":
                "false",

            charset:
                "utf-8",

            referer:
                TASK_REFERER,

            "user-agent":
                UA,

            accept:
                "*/*",

            ...ov
        };
    }


    async bizRequest(
        method,
        url,
        bodyObj,
        extraHeaders = {}
    ) {

        const time =
            Date.now().toString();

        const nonce =
            genNonce();

        const opts = {

            method,
            url,

            headers: {
                ...this.bizHeaders(extraHeaders),
                nonce,
                time,
                signature:
                    bizSignature(time, nonce)
            },

            timeout: 20000,

            validateStatus:
                () => true
        };

        if (bodyObj !== undefined) {

            opts.data =
                typeof bodyObj === "string"
                    ? bodyObj
                    : JSON.stringify(bodyObj);
        }

        return (
            await axios.request(opts)
        ).data || {};
    }


    async bizGet(url, h) {
        return this.bizRequest(
            "GET",
            url,
            undefined,
            h
        );
    }


    async bizPostJson(url, body, h) {
        return this.bizRequest(
            "POST",
            url,
            body,
            h
        );
    }


    async bizRequestNoSign(
        method,
        url,
        bodyObj,
        extraHeaders = {}
    ) {

        const time =
            Date.now().toString();

        const nonce =
            genNonce();

        const opts = {

            method,
            url,

            headers: {
                ...this.bizHeaders(extraHeaders),
                nonce,
                time,
                signature:
                    bizSignature(time, nonce)
            },

            timeout: 20000,

            validateStatus:
                () => true
        };

        if (bodyObj !== undefined) {

            opts.data =
                typeof bodyObj === "string"
                    ? bodyObj
                    : JSON.stringify(bodyObj);
        }

        try {

            const r =
                await axios.request(opts);

            const resp =
                r.data || {};

            if (
                Number(resp.code) === 200 &&
                resp.success !== false
            ) {

                return {
                    success: true,
                    data: resp
                };
            }

            return {
                success: false,
                data: resp
            };

        } catch (e) {

            return {
                success: false,

                data: {
                    message:
                        e.message ||
                        String(e)
                }
            };
        }
    }


    isAuthErr(resp) {

        const code =
            Number(resp?.code);

        const msg =
            String(
                resp?.message ||
                resp?.msg ||
                ""
            );

        if (
            [
                401,
                403,
                4001,
                4003,
                10401
            ].includes(code)
        ) {
            return true;
        }

        return /token|登录|未授权|失效|过期|未登录|鉴权|skey|会话/i
            .test(msg);
    }


    async authRetry(
        fn,
        label = "请求"
    ) {

        let resp =
            await fn();

        if (this.isAuthErr(resp)) {

            this.log(
                `⚠️ ${label} CK失效，刷新...`
            );

            await this.refreshToken();

            resp =
                await fn();
        }

        return resp;
    }


    // =================================================
    // 签到
    // =================================================
    async sign() {

        const detail =
            await this.authRetry(
                () =>
                    this.bizGet(
                        `${API_HOST}/task-manage-platform/api/activity/signInTask/taskDetail?activityId=${ACTIVITY_ID}`
                    ),
                "获取签到"
            );

        if (
            Number(detail.code) !== 200
        ) {

            this.log(
                "❌ 获取签到失败"
            );

            return;
        }

        const task =
            detail.data || {};

        if (task.taskId == null) {
            return;
        }

        if (
            task.todaySignInFinishFlag === 1 ||
            task.todaySignInFinishFlag === true
        ) {

            this.log(
                "✅ 今日已签到"
            );

            return;
        }

        const res =
            await this.authRetry(
                () =>
                    this.bizPostJson(
                        `${API_HOST}/task-manage-platform/api/activity/signInTask/award/receive`,
                        {
                            activityId:
                                String(ACTIVITY_ID),

                            taskId:
                                task.taskId
                        }
                    ),
                "签到"
            );

        if (
            Number(res.code) === 200
        ) {

            this.log(
                "✅ 签到成功"
            );

            return;
        }

        if (
            /已签|重复|已完成|已领取/.test(
                String(
                    res.message ||
                    res.msg ||
                    ""
                )
            )
        ) {

            this.log(
                "✅ 今日已签到"
            );

            return;
        }

        this.log(
            `❌ 签到失败：${res.message || res.msg || ""}`
        );
    }


    async getMiniCoinBalance() {

        const res =
            await this.authRetry(
                () =>
                    this.bizGet(
                        `${API_HOST}/task-manage-platform/api/virtualCoin/memberV2`
                    ),
                "查余额"
            );

        if (
            Number(res.code) === 200 &&
            res.data
        ) {

            return (
                res.data.quantity ??
                res.data.virtualCoin ??
                res.data.coin ??
                res.data.balance ??
                0
            );
        }

        return null;
    }


    // =================================================
    // 浏览任务
    // =================================================
    async getPeriodTaskList() {

        return this.bizGet(
            `${API_HOST}/task-manage-platform/api/activity/periodTask/taskDetail?activityId=${ACTIVITY_ID}&unionId=${encodeURIComponent(this.unionid)}`
        );
    }


    findTaskById(
        resp,
        taskId
    ) {

        if (
            !resp ||
            !Array.isArray(resp.data)
        ) {
            return null;
        }

        for (
            const p of resp.data
        ) {

            const ts =
                Array.isArray(p?.periodTasks)
                    ? p.periodTasks
                    : [];

            const f =
                ts.find(
                    i =>
                        Number(i?.taskId) ===
                        Number(taskId)
                );

            if (f) {
                return f;
            }
        }

        return null;
    }


    async uvClickTask(task) {

        return this.bizPostJson(
            `${API_HOST}/task-manage-platform/api/activity/task/uvClick`,
            {
                activityId:
                    String(ACTIVITY_ID),

                taskId:
                    Number(task.taskId),

                taskType:
                    Number(task.taskType)
            }
        );
    }


    async getBrowseTaskDetail(
        taskId
    ) {

        return this.bizGet(
            `${MULTI_API_HOST}/multi-configure-platform/api/activity/task/detail?activityId=${ACTIVITY_ID}&taskId=${taskId}`,
            {
                "content-pagetype":
                    LANDING_PAGE,

                "content-pagename":
                    LANDING_PAGE
            }
        );
    }


    async finishBrowseTask(
        taskId
    ) {

        return this.bizRequestNoSign(
            "POST",

            `${MULTI_API_HOST}/multi-configure-platform/api/activity/task/browse/finish`,

            {
                activityId:
                    Number(ACTIVITY_ID),

                taskId:
                    Number(taskId)
            },

            {
                "content-pagetype":
                    LANDING_PAGE,

                "content-pagename":
                    LANDING_PAGE
            }
        );
    }


    async channelStatistics(
        task,
        browsePagePath
    ) {

        if (!browsePagePath) {
            return;
        }

        const now =
            new Date();

        const pad =
            n =>
                String(n)
                    .padStart(2, "0");

        const ds =
            `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;

        const ts =
            `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;

        const urlObj =
            new URL(
                browsePagePath.startsWith("http")
                    ? browsePagePath
                    : `https://dummy.local${browsePagePath}`
            );

        const channelId =
            urlObj.searchParams.get(
                "channelId"
            ) || "";

        const configUrl =
            browsePagePath.replace(
                /^\//,
                ""
            ) +
            `&activityId=${ACTIVITY_ID}&countDown=1&taskId=${Number(task.taskId)}`;

        await this.bizRequestNoSign(
            "POST",

            `${CDN_HOST}/events/channelStatistics`,

            {
                uid:
                    Number(this.uid) ||
                    this.uid,

                openId:
                    this.openid,

                unionId:
                    this.unionid,

                channelId:
                    String(channelId),

                intoTime:
                    `${ds} ${ts}`,

                sceneId:
                    1089,

                minisoGroup:
                    "",

                groupCode:
                    "channelStatistics",

                latitude:
                    "",

                longitude:
                    "",

                updateDate:
                    `${ds} ${ts}`,

                config:
                    `nickName=微信用户,enterDate=${ds},enterTime=${ts},url=${configUrl}`
            },

            {
                "content-pagetype":
                    LANDING_PAGE,

                "content-pagename":
                    LANDING_PAGE
            }

        ).catch(() => {});
    }


    async receivePeriodTaskAward(
        taskId
    ) {

        return this.bizPostJson(
            `${API_HOST}/task-manage-platform/api/activity/periodTask/award/receive`,

            {
                activityId:
                    String(ACTIVITY_ID),

                taskId:
                    Number(taskId)
            }
        );
    }


    async checkPeriodTaskFinished(
        taskId
    ) {

        const resp =
            await this.authRetry(
                () =>
                    this.getPeriodTaskList(),
                "复查任务"
            );

        if (
            Number(resp.code) !== 200
        ) {

            return {
                finished: false,
                task: null
            };
        }

        const task =
            this.findTaskById(
                resp,
                taskId
            );

        if (!task) {

            return {
                finished: false,
                task: null
            };
        }

        const ft =
            Number(
                task.periodFinishTimes ?? 0
            );

        const at =
            Number(
                task.periodAllowTimes ?? 1
            );

        return {
            finished:
                ft > 0 &&
                (
                    at <= 0 ||
                    ft >= Math.min(at, 1)
                ),

            task
        };
    }


    async doOneBrowseTask(
        task
    ) {

        const taskId =
            Number(task.taskId);

        const taskName =
            task.taskName ||
            task.taskTitle ||
            task.name ||
            `浏览任务${taskId}`;

        const defaultAward =
            task.awardName ||
            "奖励";

        const allowTimes =
            Number(
                task.periodAllowTimes ?? 1
            );

        const finishTimes =
            Number(
                task.periodFinishTimes ?? 0
            );

        const fmtLine =
            (name, award) =>
                `"${name}"已完成+${award}`;


        if (
            allowTimes > 0 &&
            finishTimes >= allowTimes
        ) {

            this.log(
                `⏭️ ${taskName} 已完成`
            );

            this.notifyLines.push(
                fmtLine(
                    taskName,
                    task.awardName ||
                    defaultAward
                )
            );

            return;
        }


        this.log(
            `▶️ ${taskName} 开始`
        );


        const uv =
            await this.authRetry(
                () =>
                    this.uvClickTask(task),
                `点击[${taskName}]`
            );

        if (
            Number(uv.code) !== 200 ||
            uv.success === false
        ) {

            this.log(
                `❌ ${taskName} 点击失败`
            );

            return;
        }


        await sleep(600);


        const det =
            await this.authRetry(
                () =>
                    this.getBrowseTaskDetail(
                        taskId
                    ),
                `详情[${taskName}]`
            );

        if (
            Number(det.code) !== 200 ||
            !det.data
        ) {

            this.log(
                `❌ ${taskName} 详情失败`
            );

            return;
        }


        const dd =
            det.data || {};

        const secs =
            Math.max(
                1,
                Number(
                    dd.browseTime ??
                    task.browseSeconds ??
                    20
                ) || 20
            );

        const pagePath =
            dd.browsePagePath ||
            task.pageJumpUrl ||
            "";


        await this.channelStatistics(
            task,
            pagePath
        );


        this.log(
            `⏳ ${taskName} 等待 ${secs + 1}s`
        );


        for (
            let r = secs + 1;
            r > 0;
            r--
        ) {

            if (
                r === secs + 1 ||
                r === 10 ||
                r === 5 ||
                r === 1
            ) {

                this.log(
                    `⏱️ 剩余 ${r}s`
                );
            }

            await sleep(1000);
        }


        const fin =
            await this.finishBrowseTask(
                taskId
            );

        if (!fin.success) {

            this.log(
                `❌ ${taskName} 上报失败`
            );

            return;
        }


        await sleep(800);
        await sleep(1000);


        let chk =
            await this.checkPeriodTaskFinished(
                taskId
            );


        if (!chk.finished) {

            await sleep(2000);

            chk =
                await this.checkPeriodTaskFinished(
                    taskId
                );
        }


        if (!chk.finished) {

            this.log(
                `❌ ${taskName} 未完成`
            );

            return;
        }


        const rt =
            chk.task || task;


        const aw =
            await this.authRetry(
                () =>
                    this.receivePeriodTaskAward(
                        taskId
                    ),
                `领奖[${taskName}]`
            );


        if (
            Number(aw.code) === 200 &&
            aw.success !== false
        ) {

            const an =
                aw?.data?.awardName ||
                rt?.awardName ||
                defaultAward;

            this.log(
                `🎉 ${fmtLine(taskName, an)}`
            );

            this.notifyLines.push(
                fmtLine(
                    taskName,
                    an
                )
            );

            return;
        }


        if (
            /已领取|重复领取|领取过/.test(
                String(
                    aw.message ||
                    aw.msg ||
                    ""
                )
            )
        ) {

            const an =
                rt?.awardName ||
                defaultAward;

            this.log(
                `ℹ️ ${taskName} 已领取`
            );

            this.notifyLines.push(
                fmtLine(
                    taskName,
                    an
                )
            );

            return;
        }


        this.log(
            `❌ ${taskName} 领奖失败：${aw.message || aw.msg || ""}`
        );
    }


    // =================================================
    // 自动识别浏览任务
    // =================================================
    isBrowseTask(task) {

        if (
            !task ||
            typeof task !== "object"
        ) {
            return false;
        }

        const taskType =
            Number(task.taskType);

        /*
         * 名创优品任务名称字段可能存在变化，
         * 所以这里不只读取 taskName。
         */
        const taskName =
            String(
                task.taskName ??
                task.taskTitle ??
                task.name ??
                task.title ??
                task.taskDesc ??
                task.description ??
                ""
            ).trim();


        /*
         * 核心判断：
         *
         * 1、任务名称/描述带“浏览”
         *    → 自动认定为浏览任务
         *
         * 2、旧接口 taskType=5
         *    → 继续兼容
         */
        if (
            /浏览/.test(taskName)
        ) {
            return true;
        }


        if (
            taskType === 5
        ) {
            return true;
        }


        /*
         * 最后再做一次兜底。
         * 防止“浏览”出现在其他任务名称字段中。
         */
        const raw =
            [
                task.taskName,
                task.taskTitle,
                task.name,
                task.title,
                task.taskDesc,
                task.description,
                task.taskTypeName,
                task.typeName
            ]
                .filter(v => v != null)
                .map(String)
                .join(" ");


        return /浏览/.test(raw);
    }


    async runBrowseTasks() {

        this.log(
            "🔎 自动检查浏览任务..."
        );


        /*
         * 第一次实时获取当前任务列表。
         */
        const resp =
            await this.authRetry(
                () =>
                    this.getPeriodTaskList(),
                "任务列表"
            );


        if (
            Number(resp.code) !== 200 ||
            !Array.isArray(resp.data)
        ) {

            this.log(
                "❌ 获取任务列表失败"
            );

            return;
        }


        /*
         * 把所有分组里面的 periodTasks
         * 全部展开。
         */
        const allTasks =
            resp.data.flatMap(
                p =>
                    Array.isArray(p?.periodTasks)
                        ? p.periodTasks
                        : []
            );


        /*
         * 自动筛选浏览任务。
         *
         * 不再写死：
         *
         * .filter(t => Number(t.taskType) === 5)
         *
         * 而是通过 isBrowseTask()
         * 自动判断。
         */
        const tasks =
            allTasks.filter(
                t => this.isBrowseTask(t)
            );


        if (!tasks.length) {

            this.log(
                "ℹ️ 当前没有检测到浏览任务"
            );

            return;
        }


        this.log(
            `📋 自动识别到 ${tasks.length} 个浏览任务`
        );


        /*
         * 防止接口返回重复任务。
         */
        const uniqueTasks = [];

        const seen = new Set();


        for (
            const task of tasks
        ) {

            const taskId =
                Number(task?.taskId);


            const key =
                Number.isFinite(taskId) &&
                taskId > 0

                    ? `id:${taskId}`

                    : `name:${String(
                        task?.taskName ||
                        task?.taskTitle ||
                        task?.name ||
                        ""
                    )}`;


            if (
                seen.has(key)
            ) {
                continue;
            }


            seen.add(key);

            uniqueTasks.push(task);
        }


        /*
         * 执行当前检测到的浏览任务。
         */
        for (
            let i = 0;
            i < uniqueTasks.length;
            i++
        ) {

            const task =
                uniqueTasks[i];


            try {

                await this.doOneBrowseTask(
                    task
                );

            } catch (e) {

                this.log(
                    `❌ [${task?.taskName || task?.taskTitle || "浏览任务"}] 异常：${e.message || e}`
                );
            }


            if (
                i < uniqueTasks.length - 1
            ) {

                await sleep(1500);
            }
        }


        /*
         * =================================================
         * 重要：
         * 
         * 名创优品可能在完成任务后：
         *
         * ① 增加浏览任务
         * ② 减少浏览任务
         * ③ 改变任务完成状态
         *
         * 所以这里重新请求任务列表。
         *
         * 最多额外检查两轮。
         * =================================================
         */
        for (
            let round = 1;
            round <= 2;
            round++
        ) {

            await sleep(1200);


            const latest =
                await this.authRetry(
                    () =>
                        this.getPeriodTaskList(),
                    `第${round}次重新检查任务`
                );


            if (
                Number(latest.code) !== 200 ||
                !Array.isArray(latest.data)
            ) {

                this.log(
                    `⚠️ 第${round}次重新检查任务失败`
                );

                break;
            }


            const latestTasks =
                latest.data
                    .flatMap(
                        p =>
                            Array.isArray(p?.periodTasks)
                                ? p.periodTasks
                                : []
                    )
                    .filter(
                        t =>
                            this.isBrowseTask(t)
                    );


            /*
             * 只处理之前没有执行过的任务。
             */
            const newTasks =
                latestTasks.filter(
                    t => {

                        const taskId =
                            Number(t?.taskId);


                        const key =
                            Number.isFinite(taskId) &&
                            taskId > 0

                                ? `id:${taskId}`

                                : `name:${String(
                                    t?.taskName ||
                                    t?.taskTitle ||
                                    t?.name ||
                                    ""
                                )}`;


                        if (
                            seen.has(key)
                        ) {
                            return false;
                        }


                        seen.add(key);

                        return true;
                    }
                );


            if (
                !newTasks.length
            ) {

                this.log(
                    "✅ 重新检查：没有新增浏览任务"
                );

                break;
            }


            this.log(
                `🔄 重新检查发现 ${newTasks.length} 个新增浏览任务`
            );


            for (
                let i = 0;
                i < newTasks.length;
                i++
            ) {

                try {

                    await this.doOneBrowseTask(
                        newTasks[i]
                    );

                } catch (e) {

                    this.log(
                        `❌ [${newTasks[i]?.taskName || newTasks[i]?.taskTitle || "浏览任务"}] 异常：${e.message || e}`
                    );
                }


                if (
                    i < newTasks.length - 1
                ) {

                    await sleep(1500);
                }
            }
        }
    }


    async pushTaskSummary() {

        try {

            const b =
                await this.getMiniCoinBalance();

            if (
                b != null &&
                b !== ""
            ) {

                this.notifyLines.push(
                    `当前mini币:${b}mini币`
                );
            }

        } catch (_) {}


        if (
            this.notifyLines.length > 1
        ) {

            finalNotifyLines.push(
                this.notifyLines.join("\n")
            );
        }
    }


    async run() {

        if (
            !this.account.ref
        ) {

            this.log(
                "❌ ref为空"
            );

            return;
        }


        try {

            await this.ensureLogin();

            await this.sign();

            await this.runBrowseTasks();

            await this.pushTaskSummary();

        } catch (e) {

            const msg =
                String(
                    e.message || e
                );


            this.log(
                `❌ 捕获到错误: ${msg}`
            );


            if (
                msg.startsWith(
                    "NO_ACCOUNT"
                )
            ) {

                finalNotifyLines.push(
                    `账号[${this.index}]\n账号未注册/激活`
                );

                return;
            }


            if (
                msg.startsWith(
                    "SKIP_ACCOUNT"
                )
            ) {

                finalNotifyLines.push(
                    `账号[${this.index}]\n${msg.replace(/^SKIP_ACCOUNT:/, "")}`
                );

                return;
            }


            finalNotifyLines.push(
                `账号[${this.index}]\n执行失败：${msg}`
            );
        }
    }
}


// =====================================================
// START
// =====================================================
!(async () => {

    if (
        !userList.length
    ) {

        console.log(
            `❌ 未找到环境变量 ${ckName}`
        );

        return;
    }


    if (
        !YYB_GO_URL
    ) {

        console.log(
            "❌ 未配置 YYB_GO_URL"
        );

        return;
    }


    console.log(
        `✅ YYB-GO：${YYB_GO_URL} | 👤 ${userList.length} 个账号`
    );


    for (
        let i = 0;
        i < userList.length;
        i++
    ) {

        await new Task(
            userList[i],
            i + 1
        ).run();


        if (
            i < userList.length - 1
        ) {

            await sleep(
                1500 +
                Math.floor(
                    Math.random() * 1500
                )
            );
        }
    }


    // =================================================
    // 统一推送一次
    // =================================================
    if (
        finalNotifyLines.length
    ) {

        await pushMessage(
            "名创优品任务通知",
            finalNotifyLines.join("\n\n")
        );

    } else {

        console.log(
            "ℹ️ 本次运行无需要推送的任务结果"
        );
    }

})().catch(
    e =>
        console.log(
            `❌ 脚本异常：${e.message || e}`
        )
);

/**
 * =====================================================
 * 免责声明
 * =====================================================
 * 1. 本脚本仅供编程学习与技术研究交流，严禁用于任何商业用途或非法目的。
 * 2. 使用者需自行承担因部署、运行本脚本产生的一切后果（包括但不限于账号异常、封禁等），作者不对此承担任何责任。
 * 3. 请自觉遵守相关法律法规及目标平台的使用规范。
 * =====================================================
 */
