package com.demo.tamper.service;

import com.demo.tamper.common.BusinessException;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

/**
 * 低频稀疏采样（1 小时 / 1 天一次拉帧）的业务入口。
 *
 * <p>两次请求之间隔 1 小时，进程内状态必然过期，所以<b>机位状态由算法层按
 * cameraId 落盘</b>（{@code algo/data/sparse/}），后端只做透传与校验。
 *
 * <p>后端不做业务计算，避免同一份判定逻辑在两个语言里各写一遍而逐渐漂移。
 */
@Service
public class SparseService {

    private static final Logger log = LoggerFactory.getLogger(SparseService.class);

    private final AlgoGatewayService gateway;

    public SparseService(AlgoGatewayService gateway) {
        this.gateway = gateway;
    }

    public Map<String, Object> meta() {
        return gateway.sparseMeta();
    }

    /**
     * 提交一次抽样。
     *
     * @param cameraId 机位 id（基线库按它落盘）
     * @param frames   本次抽样的 1~K 帧
     * @param tsMs     采样时间戳（毫秒）；<=0 时由算法层取当前时间
     * @param name     机位显示名
     * @param reset    true 表示清空该机位基线（"重设基准"）
     * @param params   判定阈值覆盖（JSON 字符串）
     */
    public Map<String, Object> sample(String cameraId, List<MultipartFile> frames, long tsMs,
                                      String name, boolean reset, String params) {
        if (cameraId == null || cameraId.isBlank()) {
            throw new BusinessException(400, "cameraId 不能为空");
        }
        if (frames == null || frames.isEmpty()) {
            throw new BusinessException(400, "至少需要上传 1 帧画面");
        }
        if (frames.size() > 12) {
            throw new BusinessException(400, "单次抽样最多 12 帧");
        }

        List<Path> tempFiles = new ArrayList<>();
        try {
            for (MultipartFile frame : frames) {
                if (frame == null || frame.isEmpty()) {
                    continue;
                }
                Path tmp = Files.createTempFile("sparse-", ".jpg");
                frame.transferTo(tmp);
                tempFiles.add(tmp);
            }
            if (tempFiles.isEmpty()) {
                throw new BusinessException(400, "上传的帧都是空文件");
            }
            return gateway.sparseSample(cameraId, tempFiles, tsMs, name, reset, params);
        } catch (IOException e) {
            throw new BusinessException(500, "暂存抽样帧失败：" + e.getMessage());
        } finally {
            for (Path tmp : tempFiles) {
                try {
                    Files.deleteIfExists(tmp);
                } catch (IOException e) {
                    log.debug("清理临时帧失败：{}", e.getMessage());
                }
            }
        }
    }

    public Map<String, Object> cameras() {
        return gateway.sparseCameras();
    }

    public Map<String, Object> state(String cameraId) {
        return gateway.sparseState(cameraId);
    }

    public Map<String, Object> reset(String cameraId) {
        return gateway.sparseReset(cameraId);
    }

    public Map<String, Object> delete(String cameraId) {
        return gateway.sparseDelete(cameraId);
    }

    public byte[] thumb(String cameraId, int slot, int index) {
        return gateway.sparseThumb(cameraId, slot, index);
    }

    /**
     * 标定参照物（静止结构：门 / 楼角 / 围墙）。
     *
     * <p>为什么需要：全图相位相关在真实监控画面里很过敏——树影、旗帜、行人车辆
     * 都会把相关峰推偏。标定几个"不会自己动"的建筑物后，移位判定改为
     * "多个参照物是否符合同一个刚体变换"，过敏会显著下降。
     *
     * <p>{@code admitFrame=true} 时，标定用的这张画面会同时作为基线样本入库
     * （由算法层先过质量闸），这样新机位冷却启动能少等一次抽样。
     */
    public Map<String, Object> setLandmarks(String cameraId, String landmarksJson,
                                            MultipartFile frame, boolean clear,
                                            boolean admitFrame) {
        if (cameraId == null || cameraId.isBlank()) {
            throw new BusinessException(400, "cameraId 不能为空");
        }
        Path tmp = null;
        try {
            if (frame != null && !frame.isEmpty()) {
                tmp = Files.createTempFile("landmark-", ".jpg");
                frame.transferTo(tmp);
            }
            return gateway.sparseSetLandmarks(cameraId, landmarksJson, tmp, clear, admitFrame);
        } catch (IOException e) {
            throw new BusinessException(500, "暂存标定画面失败：" + e.getMessage());
        } finally {
            if (tmp != null) {
                try {
                    Files.deleteIfExists(tmp);
                } catch (IOException e) {
                    log.debug("清理标定临时文件失败：{}", e.getMessage());
                }
            }
        }
    }

    public Map<String, Object> getLandmarks(String cameraId) {
        return gateway.sparseGetLandmarks(cameraId);
    }

    public Map<String, Object> clearLandmarks(String cameraId) {
        return gateway.sparseClearLandmarks(cameraId);
    }

    // ------------------------------------------------------------------ 模板库
    public Map<String, Object> listTemplates(String cameraId) {
        return gateway.sparseListTemplates(cameraId);
    }

    /**
     * 给参照物追加一套外观模板（夜间 / 雨天 / 换季 / 误报反馈）。
     *
     * <p>这是"人机共驾"的关键入口，但有风险：客户若在**被遮挡或篡改**的画面上
     * 点"加为模板"，等于把攻击学成正常。所以算法层会做质量闸与去重，
     * 这里把 force 透传上去，并把这次变更记进审计。
     */
    public Map<String, Object> addTemplate(String cameraId, int landmarkId, MultipartFile frame,
                                           String label, String source, String actor,
                                           String verdictAtTime, boolean force) {
        if (cameraId == null || cameraId.isBlank()) {
            throw new BusinessException(400, "cameraId 不能为空");
        }
        if (frame == null || frame.isEmpty()) {
            throw new BusinessException(400, "必须提供模板画面");
        }
        Path tmp = null;
        try {
            tmp = Files.createTempFile("tpl-", ".jpg");
            frame.transferTo(tmp);
            return gateway.sparseAddTemplate(cameraId, landmarkId, tmp, label, source,
                    actor, verdictAtTime, force);
        } catch (IOException e) {
            throw new BusinessException(500, "暂存模板画面失败：" + e.getMessage());
        } finally {
            if (tmp != null) {
                try {
                    Files.deleteIfExists(tmp);
                } catch (IOException e) {
                    log.debug("清理模板临时文件失败：{}", e.getMessage());
                }
            }
        }
    }

    public Map<String, Object> removeTemplate(String cameraId, String templateId, String actor) {
        return gateway.sparseRemoveTemplate(cameraId, templateId, actor);
    }

    public byte[] templatePreview(String cameraId, String templateId) {
        return gateway.sparseTemplatePreview(cameraId, templateId);
    }
}
